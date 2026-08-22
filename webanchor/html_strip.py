"""Volatile-DOM removal: raw HTML in, deterministic content text out.

Design decision (deliberate, not an accident of implementation):
:func:`strip_html` returns **extracted text, not cleaned HTML**.

That choice is what makes attribute-borne volatility disappear *structurally*
rather than by blocklist.  A nonce, a CSRF hidden-input value, a
``data-request-id``, an A/B-test bucket id, a cache-busting ``src`` query
string -- none of them can reach the output, because no attribute value ever
reaches the output.  A blocklist of "volatile attribute names" can only ever
remove the volatile attributes somebody already thought of; dropping the whole
attribute channel removes the ones nobody has seen yet.  The residual job for
this module is therefore narrow and tractable: remove non-content *subtrees*,
and normalize *structure* so that text does not run together.

Consequence: :attr:`webanchor.Policy.volatile_attrs` is **unused here**.  It is
reserved for a future HTML-preserving mode and is intentionally not consulted.

Parsing uses :class:`html.parser.HTMLParser` from the stdlib.  Regex is used
only for whitespace normalization of already-extracted text, never as a tag
parser: real-world HTML is malformed, and a regex tag parser fails on malformed
input in *input-dependent* ways -- which is exactly how two validators looking
at the same page end up with different text.

Nothing in this module recurses, uses ``random``, reads the wall clock, calls
the salted builtin ``hash()``, or iterates a ``set`` in an output-affecting
order (blueprint rules R1/R3).
"""

from decimal import Decimal
from html import unescape
from html.parser import HTMLParser
from typing import Optional

from .errors import ContentTooLarge, EmptyContent, NotTextual
from .policy import Policy
from .text import collapse_layout

__all__ = [
    "strip_html",
    "reject_non_textual",
    "DROP_SUBTREE_TAGS",
    "BOUNDARY_TAGS",
]


# The open-element stack cap and the control-character ratio used to live here
# as module constants.  Both are output-affecting, so blueprint rule R6 moved
# them onto Policy (``max_tag_depth`` / ``max_control_char_ratio``) where they
# reach ``policy_id``.  The tag sets below cannot sensibly be Policy fields;
# they are covered by ``behavior.BEHAVIOR_VERSION``, which is folded into
# ``policy_id`` for exactly that reason.

#: Elements whose entire subtree is non-content and is discarded, contents
#: included.  ``script``/``style`` are gated on policy; the rest are
#: unconditional -- there is no policy under which the innards of an
#: ``<svg>`` or an ``<object>`` are page prose.
DROP_SUBTREE_TAGS: frozenset[str] = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "canvas",
        "iframe",
        "object",
        "embed",
        "applet",
        "param",
        "source",
        "track",
    }
)

#: Block-ish elements that emit a newline boundary so that adjacent blocks do
#: not fuse: ``<p>a</p><p>b</p>`` must never become ``ab``.  Everything not
#: listed here is treated as inline and emits nothing.
BOUNDARY_TAGS: frozenset[str] = frozenset(
    {
        "p",
        "div",
        "br",
        "hr",
        "li",
        "tr",
        "td",
        "th",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "header",
        "footer",
        "nav",
        "aside",
        "main",
        "blockquote",
        "pre",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "form",
        "fieldset",
        "figure",
        "figcaption",
        "address",
        "details",
        "summary",
    }
)

#: HTML void elements: they never have an end tag, so they must never be
#: pushed onto the open-element stack.  Pushing them is the classic way a
#: hand-rolled stack parser desynchronizes on real pages.
_VOID_TAGS: frozenset[str] = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

#: C0 control characters that are legitimate in text.
_ALLOWED_CONTROLS = frozenset({"\t", "\n", "\r"})

# The layout rules (horizontal-whitespace collapsing, per-line stripping,
# blank-line squeezing) live in ``text.collapse_layout`` -- one shared
# implementation, so the stripper and the canonicalizer can never drift
# apart.  U+00A0 (from ``&nbsp;``) is Unicode whitespace and is collapsed
# there, so a non-breaking space becomes an ordinary space instead of
# surviving as an invisible byte that differs between two captures of the
# same page.

_BOUNDARY = "\n"


def _ad_tokens(value: str) -> list[str]:
    """Split an attribute value into comparable whole tokens.

    Whitespace separates class names; ``-`` and ``_`` separate word parts
    inside a single name.  Whole-token comparison is mandatory: substring
    matching on ``"ad"`` would strip ``class="download"`` and ``id="loading"``,
    silently deleting real content -- the precise failure this library exists
    to prevent.
    """
    tokens: list[str] = []
    for chunk in value.split():
        for part in chunk.replace("-", " ").replace("_", " ").split():
            tokens.append(part.lower())
    return tokens


class _TextExtractor(HTMLParser):
    """Streaming, non-recursive HTML-to-text extractor.

    ``convert_charrefs`` is left OFF so that character references arrive as
    their own events and are resolved exactly once by :func:`html.unescape`.
    With it on, an unknown reference and a resolved one are indistinguishable
    downstream, and a second pass over the output could unescape twice --
    breaking the idempotency this module promises.
    """

    def __init__(self, policy: Policy) -> None:
        super().__init__(convert_charrefs=False)
        self._policy = policy
        self._drop_tags = self._active_drop_tags(policy)
        self._ad_patterns = frozenset(
            p.lower() for p in policy.effective_ad_container_patterns()
        )
        self._max_tag_depth = policy.max_tag_depth
        self._match_ads = bool(policy.strip_ad_containers) and bool(self._ad_patterns)
        # (tag, opened_a_drop_region) frames, innermost last.
        self._stack: list[tuple[str, bool]] = []
        # Elements we refused to push because the stack was at max_tag_depth.
        # A bounded integer, so pathological nesting costs O(1) memory.
        self._overflow = 0
        self._drop_depth = 0
        self._parts: list[str] = []

    @staticmethod
    def _active_drop_tags(policy: Policy) -> frozenset[str]:
        excluded: set[str] = set()
        if not policy.strip_scripts:
            excluded.add("script")
        if not policy.strip_styles:
            excluded.add("style")
        if not excluded:
            return DROP_SUBTREE_TAGS
        return frozenset(DROP_SUBTREE_TAGS - excluded)

    # -- emission ----------------------------------------------------------

    def _emit(self, text: str) -> None:
        if self._drop_depth == 0 and text:
            self._parts.append(text)

    def _emit_boundary(self, tag: str) -> None:
        if self._drop_depth == 0 and tag in BOUNDARY_TAGS:
            self._parts.append(_BOUNDARY)

    # -- structure ---------------------------------------------------------

    def _is_ad_container(self, attrs: list[tuple[str, str | None]]) -> bool:
        if not self._match_ads:
            return False
        for raw_name, raw_value in attrs:
            if raw_value is None:
                continue
            if raw_name.lower() not in ("class", "id"):
                continue
            for token in _ad_tokens(raw_value):
                if token in self._ad_patterns:
                    return True
        return False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        name = tag.lower()

        if name in _VOID_TAGS:
            # No subtree to drop and no end tag to await; a void element in
            # DROP_SUBTREE_TAGS (source/track/param/embed) simply emits
            # nothing, which is the same outcome as dropping its subtree.
            if name not in self._drop_tags:
                self._emit_boundary(name)
            return

        opens_drop = False
        if self._drop_depth == 0:
            if name in self._drop_tags or self._is_ad_container(attrs):
                opens_drop = True

        self._emit_boundary(name)

        if len(self._stack) >= self._max_tag_depth:
            self._overflow += 1
            # We cannot track a drop region we cannot push. Depth-capped
            # documents are pathological by construction; refusing to grow is
            # the safe failure, and it never raises.
            return

        self._stack.append((name, opens_drop))
        if opens_drop:
            self._drop_depth += 1

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in _VOID_TAGS:
            return

        if self._overflow > 0:
            self._overflow -= 1
            self._emit_boundary(name)
            return

        index = -1
        for position in range(len(self._stack) - 1, -1, -1):
            if self._stack[position][0] == name:
                index = position
                break

        if index < 0:
            # Stray close tag with nothing open to match (real pages do this).
            # Ignoring it is the only safe move: popping would tear down
            # unrelated open elements.
            return

        while len(self._stack) > index:
            _, opened_drop = self._stack.pop()
            if opened_drop:
                self._drop_depth -= 1

        self._emit_boundary(name)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        name = tag.lower()
        if name in _VOID_TAGS:
            self.handle_starttag(name, attrs)
            return
        # `<div/>` is not self-closing in HTML, but treating it as an empty
        # element is strictly safer than leaving an element open forever.
        self.handle_starttag(name, attrs)
        self.handle_endtag(name)

    # -- character data ----------------------------------------------------

    def handle_data(self, data: str) -> None:
        self._emit(data)

    def handle_entityref(self, name: str) -> None:
        self._emit(_resolve_reference("&" + name + ";", "&" + name))

    def handle_charref(self, name: str) -> None:
        self._emit(_resolve_reference("&#" + name + ";", "&#" + name))

    def handle_comment(self, data: str) -> None:
        # Comments routinely carry build hashes, render timestamps and server
        # hostnames -- prime divergence sources with zero reader value.
        if self._policy.strip_comments:
            return
        self._emit(data)

    def handle_decl(self, decl: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return

    def result(self) -> str:
        return "".join(self._parts)


def _resolve_reference(reference: str, fallback: str) -> str:
    """Resolve one character reference, or return it unchanged if unknown.

    Returning ``fallback`` (the reference *without* an invented semicolon)
    matters for idempotency: ``AT&T`` at end of line parses as an entity
    reference named ``T``, and emitting ``&T;`` would make a second pass
    differ from the first.
    """
    resolved = unescape(reference)
    if resolved == reference:
        return fallback
    return resolved


def reject_non_textual(
    text: str, policy: Policy, *, url: Optional[str] = None
) -> None:
    """Raise :class:`NotTextual` if ``text`` is binary rather than a document.

    Two independent tests, in this order:

    1. **Any** NUL byte disqualifies the input outright.  There is no ratio to
       argue about: a document does not contain NUL, and a truncated or
       mis-decoded binary blob does.
    2. The share of C0 control characters (tab, newline and carriage return
       excepted -- see ``_ALLOWED_CONTROLS``) exceeds
       ``policy.max_control_char_ratio``.

    The comparison is exact ``Decimal`` arithmetic, cross-multiplied
    (``controls > ratio * length``) rather than divided.  The threshold is a
    Decimal string precisely so that this decision -- which is the difference
    between one validator returning a document and another raising -- cannot
    turn on a binary-float last digit.

    Shared by :func:`strip_html` and :func:`webanchor.detect.check_response`
    so that the two cannot drift into disagreeing about what "textual" means.
    """
    if "\x00" in text:
        raise NotTextual(
            "input contains NUL bytes; this is not a text document", url=url
        )
    controls = 0
    for char in text:
        if char < " " and char not in _ALLOWED_CONTROLS:
            controls += 1
    if not controls:
        return
    length = len(text)
    ratio = policy.control_char_ratio()
    if Decimal(controls) > ratio * Decimal(length):
        share = (Decimal(controls) * 100 / Decimal(length)).quantize(
            Decimal("0.1")
        )
        raise NotTextual(
            "input is {0}% C0 control characters, over the {1} policy "
            "limit; this is binary, not a document".format(share, ratio),
            url=url,
        )


def strip_html(html: str, policy: Policy) -> str:
    """Turn raw HTML into deterministic, content-bearing plain text.

    Removes non-content subtrees (scripts, styles, embedded objects, and --
    only when ``policy.strip_ad_containers`` is set -- ad containers), drops
    comments, resolves character references, and normalizes structural
    whitespace.  Attributes never appear in the result by construction.

    Unicode NFKC normalization, lowercasing, number banding and timestamp
    quantization are deliberately NOT done here; they are canonicalizer
    concerns and live in their own modules.  This function's responsibility is
    structure, and keeping it that way is what makes it testable in isolation.

    Raises:
        ContentTooLarge: input exceeds ``policy.max_content_bytes``.
        NotTextual: input contains NUL bytes or is mostly C0 controls.
        EmptyContent: input is empty, or nothing readable survived stripping.
            An empty result means the page carried no evidence; that must be
            loud, never an empty string quietly handed to an LLM.
    """
    if not isinstance(html, str):
        raise NotTextual(
            "strip_html expects str, got {0}".format(type(html).__name__)
        )

    size = len(html.encode("utf-8"))
    if size > policy.max_content_bytes:
        raise ContentTooLarge(
            "input is {0} bytes, over the {1}-byte policy limit".format(
                size, policy.max_content_bytes
            )
        )

    if not html:
        raise EmptyContent("input HTML is empty")

    reject_non_textual(html, policy)

    parser = _TextExtractor(policy)
    parser.feed(html)
    parser.close()

    text = collapse_layout(parser.result(), policy.collapse_whitespace)

    if not text:
        raise EmptyContent(
            "stripping produced no readable text; the page carried no evidence"
        )
    return text
