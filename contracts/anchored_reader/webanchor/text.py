"""Unicode and whitespace canonicalization.

The characters this module folds are not exotic.  They are what CDNs,
templating engines, CMS "smart quote" filters and copy-paste pipelines emit
*inconsistently* for the same rendered page:

* A non-breaking space where the previous deploy emitted an ordinary one,
  because an editor pressed Alt+Space or a template used ``&nbsp;``.
* A zero-width space injected by a line-breaking helper, invisible in the
  browser and invisible in a diff, but a different byte.
* A curly apostrophe from a smart-quote filter on one render and a straight
  one on another, because the filter runs per-request and skips text that has
  already been transformed.
* A BOM prepended by one origin server and not another.

Every one of those is a byte difference with zero semantic difference, which
means every one of them is a fingerprint divergence between a leader and a
validator looking at the same page.

What NFKC does and does not do
------------------------------
``unicodedata.normalize("NFKC", ...)`` is necessary but **not sufficient**, and
relying on it silently is a mistake.  Measured behavior (CPython, Unicode 15+):

* NFKC **does** fold: NBSP U+00A0, narrow NBSP U+202F, thin/hair/en/em/figure
  space, ideographic space U+3000 -> ASCII space; ellipsis U+2026 -> ``...``;
  double prime U+2033 -> two U+2032 primes; non-breaking hyphen U+2011 ->
  hyphen U+2010.
* NFKC **does not** touch: zero-width space U+200B, ZWNJ U+200C, ZWJ U+200D,
  word joiner U+2060, BOM U+FEFF, soft hyphen U+00AD, en dash U+2013, em dash
  U+2014, horizontal bar U+2015, figure dash U+2012, minus sign U+2212,
  hyphen U+2010, curly single/double quotes U+2018-U+201F, prime U+2032,
  modifier apostrophe U+02BC.

So the invisible-character class and the dash/quote class are handled here
explicitly, by table, and the table is versioned by
:data:`webanchor.behavior.BEHAVIOR_VERSION`.  Note also that the caller may
select a non-NFKC ``unicode_form`` (NFC/NFD/NFKD), under which even the folds
NFKC would have performed do not happen -- another reason not to lean on it.

Idempotency
-----------
:func:`canonicalize_text` is idempotent: ``f(f(x)) == f(x)`` for all ``x``.
This is load-bearing, not a nicety.  The pipeline may be re-run over already
normalized text (re-anchoring stored evidence, a validator re-checking a
leader's text), and a non-idempotent canonicalizer would make the second run
disagree with the first.  It holds by construction -- every replacement target
is ASCII, and ASCII is a fixed point of all four normalization forms -- and is
tested over a deliberately nasty corpus.
"""

import re
import unicodedata

from .policy import Policy

__all__ = ["canonicalize_text", "collapse_layout", "UNICODE_FOLD_MAP"]


#: Characters deleted outright: invisible, semantically empty, and emitted
#: inconsistently by real publishing pipelines.  Deleting rather than
#: space-mapping is the right call -- a zero-width space inside a word is a
#: line-break hint, not a token boundary, and mapping it to a space would
#: split one word into two.
_DELETE_CHARS = (
    "\u200b"  # ZERO WIDTH SPACE
    "\u200c"  # ZERO WIDTH NON-JOINER
    "\u200d"  # ZERO WIDTH JOINER
    "\u2060"  # WORD JOINER
    "\ufeff"  # ZERO WIDTH NO-BREAK SPACE / BOM
    "\u00ad"  # SOFT HYPHEN
    "\u180e"  # MONGOLIAN VOWEL SEPARATOR
)

#: Characters folded to an ordinary ASCII space.  NFKC covers most of these,
#: but only under NFKC -- an NFC policy would leave every one of them intact.
_SPACE_CHARS = (
    "\u00a0"  # NO-BREAK SPACE
    "\u1680"  # OGHAM SPACE MARK
    "\u2000\u2001\u2002\u2003\u2004\u2005"  # EN QUAD .. FOUR-PER-EM SPACE
    "\u2006\u2007\u2008\u2009\u200a"  # SIX-PER-EM .. HAIR SPACE
    "\u202f"  # NARROW NO-BREAK SPACE
    "\u205f"  # MEDIUM MATHEMATICAL SPACE
    "\u3000"  # IDEOGRAPHIC SPACE
    "\u0085"  # NEXT LINE -- treated as horizontal space, not a line break:
    #           promoting it to a newline would invent paragraph structure.
)

#: Dash-like characters folded to ASCII ``-``.  Left alone by every
#: normalization form; a page that swaps an en dash for a hyphen between two
#: renders is otherwise a guaranteed fingerprint divergence.
_DASH_CHARS = (
    "\u2010"  # HYPHEN
    "\u2011"  # NON-BREAKING HYPHEN
    "\u2012"  # FIGURE DASH
    "\u2013"  # EN DASH
    "\u2014"  # EM DASH
    "\u2015"  # HORIZONTAL BAR
    "\u2043"  # HYPHEN BULLET
    "\u2212"  # MINUS SIGN
    "\ufe58"  # SMALL EM DASH
    "\ufe63"  # SMALL HYPHEN-MINUS
    "\uff0d"  # FULLWIDTH HYPHEN-MINUS
)

#: Single-quote-like characters folded to ASCII ``'``.
_SINGLE_QUOTE_CHARS = (
    "\u2018\u2019\u201a\u201b"  # curly / low / reversed single quotes
    "\u2032"  # PRIME
    "\u2035"  # REVERSED PRIME
    "\u02bc"  # MODIFIER LETTER APOSTROPHE
    "\u02b9"  # MODIFIER LETTER PRIME
    "\u00b4"  # ACUTE ACCENT (used as an apostrophe in the wild)
    "\uff07"  # FULLWIDTH APOSTROPHE
)

#: Double-quote-like characters folded to ASCII ``"``.  Guillemets
#: (U+00AB/U+00BB) are deliberately NOT folded: in French and several other
#: locales they are the *primary* quotation marks and are not interchangeable
#: with ASCII quotes, so folding them would destroy information rather than
#: normalize a rendering accident.
_DOUBLE_QUOTE_CHARS = (
    "\u201c\u201d\u201e\u201f"  # curly / low / reversed double quotes
    "\u2033"  # DOUBLE PRIME
    "\u2036"  # REVERSED DOUBLE PRIME
    "\u02ba"  # MODIFIER LETTER DOUBLE PRIME
    "\u3003"  # DITTO MARK
    "\uff02"  # FULLWIDTH QUOTATION MARK
)


def _build_fold_map() -> dict[int, str | None]:
    table: dict[int, str | None] = {}
    for char in _DELETE_CHARS:
        table[ord(char)] = None
    for char in _SPACE_CHARS:
        table[ord(char)] = " "
    for char in _DASH_CHARS:
        table[ord(char)] = "-"
    for char in _SINGLE_QUOTE_CHARS:
        table[ord(char)] = "'"
    for char in _DOUBLE_QUOTE_CHARS:
        table[ord(char)] = '"'
    return table


#: ``str.translate`` table for the explicit fold.  Every value is ASCII (or a
#: deletion), which is what makes applying it twice a no-op.
UNICODE_FOLD_MAP: dict[int, str | None] = _build_fold_map()

#: Horizontal whitespace: every whitespace character except the newline.
_HORIZONTAL_WS = re.compile(r"[^\S\n]+")

_THREE_PLUS_NEWLINES = re.compile(r"\n{3,}")


def normalize_line_endings(text: str) -> str:
    """Fold CRLF and lone CR to LF.

    Done before anything else, unconditionally and regardless of policy.  A
    line ending is the single most common byte-level difference between two
    captures of the same document (proxy rewriting, editor defaults, origin
    platform), and every downstream rule -- per-line stripping, blank-line
    squeezing, boundary emission -- is defined in terms of ``\\n``.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def collapse_layout(text: str, collapse_whitespace: bool) -> str:
    """Normalize structural whitespace: the shared layout rule.

    Horizontal runs collapse to one space (when enabled), every line is
    stripped, runs of three or more newlines squeeze to exactly two, and the
    result is stripped.  Squeezing to two rather than one preserves the
    paragraph boundary an LLM reads, while erasing the arbitrary blank-line
    count that markup indentation produces.

    Shared by :mod:`webanchor.html_strip` and this module on purpose: two
    copies of a whitespace rule is two constants that can drift apart, which
    is precisely the R6 hazard.
    """
    text = normalize_line_endings(text)
    if collapse_whitespace:
        text = _HORIZONTAL_WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _THREE_PLUS_NEWLINES.sub("\n\n", text)
    return text.strip()


def canonicalize_text(text: str, policy: Policy) -> str:
    """Fold a page's text to a byte-stable canonical form.

    Stage order, and why:

    1. **Line endings** -- ``\\r\\n`` and ``\\r`` become ``\\n``, first, so
       every later rule sees one line-break representation.
    2. **Explicit fold** (:data:`UNICODE_FOLD_MAP`) -- invisible characters
       deleted, space variants, dashes and quotes folded to ASCII.  Applied
       *before* normalization so that, e.g., double prime U+2033 becomes
       ``"`` directly instead of being expanded by NFKC into two primes that a
       later pass would then have to re-fold.
    3. **Unicode normalization** in ``policy.unicode_form``.
    4. **Explicit fold again** -- normalization can synthesize characters in
       the table (NFKC expands U+2033 into primes, U+2011 into U+2010); a
       second pass costs one ``translate`` and closes that gap.
    5. **Lowercase**, if ``policy.lowercase``.
    6. **Normalization again** -- ``str.lower()`` is not guaranteed to
       preserve the chosen normal form (U+0130 is the classic offender), so
       re-normalizing is what makes the *output* normalized rather than merely
       the input to the casefold.
    7. **Layout collapse**, if ``policy.collapse_whitespace``.

    Idempotent for every input; see the module docstring.
    """
    text = normalize_line_endings(text)
    text = text.translate(UNICODE_FOLD_MAP)
    text = unicodedata.normalize(policy.unicode_form, text)
    text = text.translate(UNICODE_FOLD_MAP)
    if policy.lowercase:
        text = text.lower()
        text = unicodedata.normalize(policy.unicode_form, text)
    return collapse_layout(text, policy.collapse_whitespace)
