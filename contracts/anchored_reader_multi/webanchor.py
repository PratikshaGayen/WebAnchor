"""webanchor -- flattened single-module build for py-genlayer-multi deployment.

GenVM's py-genlayer-multi runner does not correctly support a NESTED
subpackage (a directory with its own __init__.py) inside a deployed
multi-file contract package -- confirmed against a live GenLayer Studio
node with a minimal repro (see the integration report): `from . import sub`
where `sub/` is a subdirectory either raises `ImportError: cannot import
name 'sub' from partially initialized module 'contract' (most likely due
to a circular import)` or produces an empty namespace module with no
attributes, depending on structure. A FLAT sibling module (a .py file
directly next to __init__.py, no subdirectory) works fine.

This file is therefore a mechanical concatenation of the vendored
`webanchor/` package's submodules (errors, behavior, policy, timestamps,
text, numbers, html_strip, detect, fingerprint, evidence, pipeline, fetch)
in dependency order, with their internal `from .X import Y` lines removed
(everything now shares one module namespace, so cross-references resolve
directly). It is generated, not hand-maintained -- the real source of
truth remains the top-level `webanchor/` package and
`contracts/anchored_reader/webanchor/` (the subpackage-vendored copy used
by tests/direct/). This flattened copy exists ONLY to work around the
live-deploy nested-subpackage limitation.
"""



# ============================================================================
# --- from webanchor/errors.py ---
# ============================================================================

"""Typed error taxonomy for WebAnchor.

Every failure path in WebAnchor raises a subclass of :class:`WebAnchorError`
(blueprint rule R4: fail loudly, never silently).  Errors carry a stable,
machine-readable ``code`` so that a contract can branch on the failure kind
without string-matching a human message.
"""

from typing import Optional

__all__ = [
    "WebAnchorError",
    "FetchError",
    "RateLimited",
    "UpstreamUnavailable",
    "NotFound",
    "Forbidden",
    "UnexpectedStatus",
    "NetworkError",
    "ContentError",
    "EmptyContent",
    "ContentTooLarge",
    "NotTextual",
    "BotWallDetected",
    "SoftErrorPage",
    "PolicyError",
    "UnstableContent",
    "PolicyMismatch",
    "ERROR_BY_CODE",
    "from_status",
]


class WebAnchorError(Exception):
    """Base class for every WebAnchor failure."""

    code: str = "webanchor.error"

    def __init__(self, detail: str = "", *, url: Optional[str] = None) -> None:
        self.detail: str = detail
        self.url: Optional[str] = url
        super().__init__(self._render())

    def _render(self) -> str:
        base = "[{0}] {1}".format(self.code, self.detail)
        if self.url:
            return "{0} (url={1})".format(base, self.url)
        return base

    def __str__(self) -> str:
        return self._render()

    def as_dict(self) -> dict[str, str]:
        """Calldata-safe primitive view of this error."""
        return {
            "code": self.code,
            "detail": self.detail,
            "url": self.url if self.url is not None else "",
        }


# --------------------------------------------------------------------------
# Fetch-layer failures: the HTTP round trip did not yield usable content.
# --------------------------------------------------------------------------


class FetchError(WebAnchorError):
    code = "fetch.error"


class RateLimited(FetchError):
    code = "fetch.rate_limited"


class UpstreamUnavailable(FetchError):
    code = "fetch.upstream_unavailable"


class NotFound(FetchError):
    code = "fetch.not_found"


class Forbidden(FetchError):
    code = "fetch.forbidden"


class UnexpectedStatus(FetchError):
    code = "fetch.unexpected_status"


class NetworkError(FetchError):
    code = "fetch.network"


# --------------------------------------------------------------------------
# Content-layer failures: bytes arrived, but they are not anchorable evidence.
# --------------------------------------------------------------------------


class ContentError(WebAnchorError):
    code = "content.error"


class EmptyContent(ContentError):
    code = "content.empty"


class ContentTooLarge(ContentError):
    code = "content.too_large"


class NotTextual(ContentError):
    code = "content.not_textual"


class BotWallDetected(ContentError):
    code = "content.bot_wall"


class SoftErrorPage(ContentError):
    code = "content.soft_error"


# --------------------------------------------------------------------------
# Policy-layer failures: normalization cannot yield a consensus-safe result.
# --------------------------------------------------------------------------


class PolicyError(WebAnchorError):
    code = "policy.error"


class UnstableContent(PolicyError):
    code = "policy.unstable"


class PolicyMismatch(PolicyError):
    code = "policy.mismatch"


def _walk_subclasses(root: type) -> list[type]:
    """Depth-first walk of every subclass of ``root``, ``root`` included."""
    found: list[type] = [root]
    seen: set[str] = {root.__qualname__}
    stack: list[type] = [root]
    while stack:
        current = stack.pop()
        for sub in current.__subclasses__():
            if sub.__qualname__ in seen:
                continue
            seen.add(sub.__qualname__)
            found.append(sub)
            stack.append(sub)
    return found


def _build_error_index() -> dict[str, type[WebAnchorError]]:
    index: dict[str, type[WebAnchorError]] = {}
    for cls in _walk_subclasses(WebAnchorError):
        code = cls.code
        existing = index.get(code)
        if existing is not None and existing is not cls:
            raise RuntimeError(
                "duplicate WebAnchor error code {0!r}: {1} and {2}".format(
                    code, existing.__name__, cls.__name__
                )
            )
        index[code] = cls
    return index


#: Every error code mapped to the class that owns it, derived programmatically.
ERROR_BY_CODE: dict[str, type[WebAnchorError]] = _build_error_index()


def from_status(status: int, *, url: Optional[str] = None) -> Optional[FetchError]:
    """Map an HTTP status code to the matching :class:`FetchError` instance.

    Returns ``None`` for 2xx, which is the only "no error" band.
    """
    if status == 429:
        return RateLimited("upstream rate limited the request", url=url)
    if status == 404:
        return NotFound("upstream returned 404", url=url)
    if status in (401, 403):
        return Forbidden("upstream refused the request ({0})".format(status), url=url)
    if 200 <= status <= 299:
        return None
    if 500 <= status <= 599:
        return UpstreamUnavailable(
            "upstream server error ({0})".format(status), url=url
        )
    return UnexpectedStatus("unexpected HTTP status {0}".format(status), url=url)



# ============================================================================
# --- from webanchor/behavior.py ---
# ============================================================================

"""The single integer that versions WebAnchor's *behavior*, not its API.

Blueprint rule R6: **every constant that can change output must be versioned
into ``policy_id``.**

Some normalization behavior cannot reasonably live in :class:`~webanchor.Policy`
as a tunable field -- the set of HTML tags whose subtree is dropped, the set of
tags that emit a line boundary, the exact whitespace-collapsing rules, the
canonical rendering of a quantized timestamp.  Those are implementation
constants, but they are *output-affecting* implementation constants, and that
makes them a consensus hazard: a leader on WebAnchor 0.1.0 and a validator on
0.2.0 would normalize the same bytes differently, produce different
fingerprints, and have no way to attribute the disagreement to a version skew
rather than to the page changing underneath them.  Worse, in the raising cases
(control-character ratio, size caps) one node returns text while the other
raises -- silent, asymmetric divergence.

``BEHAVIOR_VERSION`` closes that hole.  It is folded into
:meth:`webanchor.Policy.canonical_json` and therefore into every ``policy_id``
and every fingerprint.  Two nodes running different behavior versions produce
*visibly* different policy ids, which is a diagnosable failure instead of a
silent one.

Bump the integer whenever a change alters the output of the normalization
pipeline for **some** input
=====================================================================

"Some input" is the bar, not "typical input".  If you cannot prove that every
possible input produces byte-identical output before and after your change,
bump it.  Concretely, bump when you change any of:

1. ``html_strip.DROP_SUBTREE_TAGS`` -- adding or removing a dropped subtree.
2. ``html_strip.BOUNDARY_TAGS`` -- adding or removing a newline-emitting tag.
3. ``html_strip._VOID_TAGS`` -- void-element handling shifts the tag stack.
4. Depth-cap *semantics* (the cap's value is ``Policy.max_tag_depth``, but
   what happens on overflow is behavior).
5. Control-character handling: which characters count as controls, whether
   NUL raises, how the ratio is compared against ``Policy.max_control_char_ratio``.
6. Whitespace rules: horizontal-whitespace collapsing, per-line stripping,
   blank-line squeezing, line-ending normalization.
7. The unicode fold table in ``text.py`` -- dashes, quotes, primes,
   zero-width characters, space variants.
8. Number detection or rendering in ``numbers.py``: the match pattern, the
   thousands-separator ambiguity rule, the rounding mode, the band token
   format, the canonical decimal rendering.
9. Timestamp detection or rendering in ``timestamps.py``: which patterns are
   recognized, the relative-time placeholder, the redaction placeholder, the
   canonical quantized format, the assumed timezone for naive inputs.
10. The stage order in ``pipeline.normalize``.
11. Any marker tuple in ``detect.py`` -- ``BOT_WALL_STRONG_MARKERS``,
    ``BOT_WALL_WEAK_MARKERS``, ``CHALLENGE_SERVER_TOKENS``,
    ``SOFT_ERROR_PHRASES`` -- or any of the thresholds beside them.  Adding a
    single marker flips some page from "returns an Evidence" to "raises
    ``BotWallDetected``".  That is the asymmetric, silent divergence R6 exists
    to prevent, and it is the reason those lists are module constants covered
    by this version rather than a set someone extends at call time.

Do **not** bump for: docstring edits, refactors provably output-identical,
new tests, new ``Policy`` fields (those already change ``policy_id`` on their
own via :meth:`~webanchor.Policy.to_dict`), or performance work.

Bumping invalidates every previously anchored fingerprint.  That is the point:
an invalidated fingerprint is a loud failure, and a loud failure is the entire
product.

Every bucketing scheme leaves residual divergence
=================================================

This section is the single, general statement of a property that WebAnchor's
three bucketing schemes all share.  It lives here, once, and the modules that
implement those schemes cross-reference it rather than restating it -- three
copies of a caveat drift, and a drifted caveat is worse than none.

The schemes are:

* **Timestamp quantization** (:mod:`webanchor.timestamps`, ``timestamp_mode``
  ``"quantize"``) -- flooring an instant to ``timestamp_quantum_seconds``.
* **Significant-digit rounding** (:mod:`webanchor.numbers`,
  ``number_band_mode`` ``"significant"``) -- snapping a value onto the lattice
  of numbers with ``number_significant_digits`` significant digits.
* **Grid banding** (:mod:`webanchor.numbers`, ``number_band_mode``
  ``"grid"``) -- flooring a value onto a lattice of width
  ``number_grid_step`` and printing the bucket.

All three work the same way: partition a continuum into buckets, and emit the
bucket instead of the reading.  Two validators whose readings fall in one
bucket agree exactly.  Two validators whose readings **straddle a bucket
edge** disagree exactly as completely as if there had been no bucketing at
all -- the outputs are different strings, ``strict_eq`` compares strings, and
the round fails.  Widening the bucket does not change the shape of this; it
only changes how often an edge falls between two readings::

    residual divergence risk  ~=  spread / bucket_width

where *spread* is the range of readings across the validator set for the value
in question (the wall-clock window between fetches, for timestamps; the drift
of the underlying number, for prices and counters), and *bucket_width* is the
quantum, the significant-digit step at that magnitude, or the grid step.  The
approximation holds while ``spread << bucket_width``; once the spread
approaches the bucket width, disagreement is the normal case.

Three consequences worth stating plainly:

1. **The risk never reaches zero.** No finite bucket width eliminates it. A
   quantum of a year still has 365 edges' worth of pages that straddle one.
2. **It compounds across values.** ``n`` independent bucketed values on one
   page multiply the per-value survival probability, so a page with ten
   timestamps fails roughly ten times as often as a page with one.
3. **The failures are correlated, not independent.** Validators fetch in a
   burst. A burst that happens to sit on a bucket edge splits the validator
   set, which is the worst case rather than the average one -- the naive
   per-validator probability understates it.

**Redaction is the only total guarantee.** ``timestamp_mode="redact"``
replaces the instant with ``[timestamp]``, a string that does not depend on
when anyone fetched, so its residual risk is exactly zero rather than merely
small. There is no numeric equivalent shipped today because a redacted price
carries no evidence; if you need a zero-risk numeric path, the answer is to
not put the volatile number in the anchored text at all.

Choose accordingly: bucket when the value is the evidence and you can size the
bucket against your spread, redact when it is page furniture. Paying a
permanent consensus risk for furniture is a bad trade, which is why
``redact`` is the default timestamp mode and ``none`` the default number mode.
"""

__all__ = ["BEHAVIOR_VERSION"]

#: Version of the output-affecting behavior of the normalization pipeline.
#: See the module docstring for the bump checklist.
#:
#: History
#: -------
#: 1. The normalization stages: stripping, text canonicalization, timestamps, numbers.
#: 2. Fetch/detection: the ``detect.py`` marker tables entered the output-affecting set
#:    (item 11 above), and number banding's ``percent`` mode was replaced by
#:    ``grid``.  Both change what the library does with some input, and the
#:    detection tables are not reachable from any :class:`~webanchor.Policy`
#:    field, so this constant is the only thing that can carry them into
#:    ``policy_id``.  Every fingerprint anchored under version 1 is
#:    invalidated, loudly, which is the designed behavior.
BEHAVIOR_VERSION = 2



# ============================================================================
# --- from webanchor/policy.py ---
# ============================================================================

"""Policy: every tunable normalization knob, plus its deterministic identity.

Blueprint rule R5 -- policy is explicit and versioned.  The ``policy_id`` is
folded into every fingerprint, so two validators running different policies
produce *visibly* different fingerprints instead of silently diverging ones.

Blueprint rule R6 -- every constant that can change output is versioned into
``policy_id``.  Output-affecting knobs that a caller might reasonably want to
tune live here as fields; output-affecting constants that cannot be fields
(tag sets, whitespace rules, token formats) are covered by
:data:`webanchor.behavior.BEHAVIOR_VERSION`, which is folded into
:meth:`Policy.canonical_json` below.
"""

import hashlib
import json
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any


__all__ = [
    "Policy",
    "POLICY_ID_PREFIX",
    "POLICY_ID_HEX_LEN",
    "DEFAULT_MAX_TAG_DEPTH",
    "DEFAULT_MAX_CONTROL_CHAR_RATIO",
    "DEFAULT_NUMBER_GRID_STEP",
]

#: Prefix of every policy identifier; bumped when the id *format* changes.
POLICY_ID_PREFIX = "p1:"

#: Hex characters of sha256 kept in a policy_id (128 bits). Do not shrink:
#: a policy_id collision lets two validators normalize differently while
#: agreeing on a fingerprint, which is the silent divergence R5 forbids.
POLICY_ID_HEX_LEN = 32

#: Default open-element stack cap.  Lives here rather than in ``html_strip``
#: so that it reaches ``policy_id`` (R6); ``html_strip`` reads it off the
#: Policy instance, never as a module constant.
DEFAULT_MAX_TAG_DEPTH = 1000

#: Default share of C0 control characters above which input is judged binary.
#:
#: A **string**, parsed with :class:`decimal.Decimal` at the point of use.
#: See :attr:`Policy.max_control_char_ratio` for why this is not a float.
DEFAULT_MAX_CONTROL_CHAR_RATIO = "0.05"

#: Default width of one banding bucket in ``grid`` mode, as a Decimal string.
DEFAULT_NUMBER_GRID_STEP = "1"

#: Banding modes, in a fixed tuple (R3: never a set).
#:
#: ``percent`` was REMOVED after benchmarking and is deliberately not kept
#: as a deprecated alias.  It banded ``+/- p%`` around *each validator's own
#: reading*, so two validators reading 1000 and 1050 produced
#: ``[900~1100]`` and ``[945~1155]`` -- overlapping intervals, different
#: strings, and ``strict_eq`` compares strings.  A convergence feature that
#: does not converge is a footgun, and a footgun with a friendly name is worse
#: than a breaking change in a pre-1.0 library.  ``grid`` is its replacement
#: and actually converges: both readings land in the same bucket.
_NUMBER_BAND_MODES = ("none", "significant", "grid")
_UNICODE_FORMS = ("NFC", "NFD", "NFKC", "NFKD")
_TIMESTAMP_MODES = ("none", "quantize", "redact")

_DEFAULT_VOLATILE_ATTRS: tuple[str, ...] = (
    "nonce",
    "integrity",
    "csrf",
    "data-session",
    "data-request-id",
    "data-timestamp",
    "data-nonce",
)

_DEFAULT_AD_CONTAINER_PATTERNS: tuple[str, ...] = (
    "ad",
    "ads",
    "adbox",
    "advert",
    "advertisement",
    "banner",
    "sponsor",
    "sponsored",
    "promo",
    "promoted",
)


def _parse_decimal_field(name: str, raw: Any) -> Decimal:
    """Parse a Decimal-string Policy field, or raise :class:`PolicyError`.

    Rejects ``float`` outright rather than coercing it.  Accepting a float and
    calling ``Decimal(str(value))`` would work today and silently reintroduce
    the float path the moment someone passed a computed value, so the type is
    refused at the boundary where the error message can still say why.
    NaN and the infinities are rejected too: they compare in ways that would
    make a threshold check quietly meaningless.
    """
    if isinstance(raw, float):
        raise PolicyError(
            "{0} must be a Decimal STRING, not a float; got {1!r}. "
            "Binary floats are forbidden in numeric paths (R3): pass "
            "{2!r} instead".format(name, raw, repr(raw))
        )
    if not isinstance(raw, str):
        raise PolicyError(
            "{0} must be a Decimal string, got {1}".format(
                name, type(raw).__name__
            )
        )
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError, ArithmeticError):
        raise PolicyError(
            "{0} must be a Decimal string, got {1!r}".format(name, raw)
        ) from None
    if not value.is_finite():
        raise PolicyError(
            "{0} must be a finite Decimal string, got {1!r}".format(name, raw)
        )
    return value


@dataclass(frozen=True)
class Policy:
    """An immutable, hashable-by-content description of normalization behavior."""

    schema_version: int = 1
    strip_scripts: bool = True
    strip_styles: bool = True
    strip_comments: bool = True
    strip_ad_containers: bool = False
    """Drop whole subtrees whose class/id token-matches the effective ad patterns.

    Defaults to ``False`` -- stripping is CONSERVATIVE by default.  Aggressive
    class-name heuristics silently eat real content on sites that use generic
    names like ``promo``, ``banner`` or ``sponsor`` for editorial modules, and
    silent content loss is the exact failure mode this library exists to
    prevent.  A validator that quietly drops the paragraph carrying the fact
    under consensus is worse than one that keeps a rotating ad: the ad is
    visible divergence, the missing paragraph is not.  Opt in via
    :meth:`Policy.strict` when you know the page.
    """

    ad_container_patterns: tuple[str, ...] = _DEFAULT_AD_CONTAINER_PATTERNS
    """Whole-token class/id names treated as ad containers -- REPLACES the defaults.

    Setting this field discards the built-in list entirely.  That is deliberate
    and is what you want when you know the page and need exactly one pattern.
    If instead you want the built-ins *plus* your own, use
    :attr:`extra_ad_container_patterns`, which is unioned rather than
    substituted -- overwriting this field by accident and silently losing all
    ten defaults is a real footgun and the reason both fields exist.

    Consulted ONLY when :attr:`strip_ad_containers` is ``True``.  Matching is
    whole-token, never substring: ``class="download"`` and ``id="loading"``
    both contain the substring ``ad`` and must survive.  Class values are split
    on whitespace, then both class and id tokens are split on ``-`` and ``_``
    and compared case-insensitively.
    """

    extra_ad_container_patterns: tuple[str, ...] = ()
    """Additional ad-container patterns, UNIONED with :attr:`ad_container_patterns`.

    The additive counterpart to the substitutive field above.  The effective
    set is :meth:`effective_ad_container_patterns`, which is always a *sorted
    tuple*: an unsorted set would make output depend on hash iteration order,
    which blueprint rule R3 forbids.
    """

    volatile_attrs: tuple[str, ...] = _DEFAULT_VOLATILE_ATTRS
    unicode_form: str = "NFKC"
    collapse_whitespace: bool = True
    lowercase: bool = False
    number_band_mode: str = "none"
    """How numbers are canonicalized: ``none`` | ``significant`` | ``grid``.

    Both non-``none`` modes are *bucketing* schemes and both inherit the
    residual-divergence property documented once in
    :mod:`webanchor.behavior` (section "Every bucketing scheme leaves residual
    divergence").  Neither is a guarantee; only redaction is.
    """

    number_grid_step: str = DEFAULT_NUMBER_GRID_STEP
    """Width of one ``grid`` bucket, as a **Decimal string** -- never a float.

    Parsed with ``Decimal(self.number_grid_step)`` at the point of use.  A
    float field would reintroduce exactly the build-dependent last digit that
    :mod:`webanchor.numbers` exists to eliminate: ``0.1`` as a binary float is
    ``0.1000000000000000055511151231257827``, and a bucket edge computed from
    that value can fall on either side of a reading depending on the CPython
    build.  A string is exact, and its *text* is what reaches ``policy_id``,
    so two validators cannot disagree about the grid while agreeing about the
    policy.

    Must parse as a finite Decimal strictly greater than zero.
    """

    number_significant_digits: int = 3
    timestamp_mode: str = "redact"
    """How absolute timestamps are handled: ``redact`` | ``quantize`` | ``none``.

    Defaults to ``redact`` because redaction is the only mode with a *total*
    guarantee.  See :mod:`webanchor.behavior`, section "Every bucketing scheme
    leaves residual divergence", for the boundary-straddle limitation that
    makes ``quantize`` -- like ``significant`` and ``grid`` banding -- a
    risk-reduction rather than an elimination.
    Relative expressions ("2 hours ago") are replaced in both ``redact`` and
    ``quantize``; only ``none`` leaves them alone.
    """

    timestamp_quantum_seconds: int = 3600
    max_content_bytes: int = 2_000_000
    max_tag_depth: int = DEFAULT_MAX_TAG_DEPTH
    """Hard cap on the HTML open-element stack.

    Output-affecting, therefore a Policy field rather than a module constant
    in ``html_strip`` (R6): two validators disagreeing about the cap would
    disagree about the text of a deeply nested page.
    """

    max_control_char_ratio: str = DEFAULT_MAX_CONTROL_CHAR_RATIO
    """Share of C0 control characters above which input is rejected as binary.

    A **Decimal string** in ``[0, 1]``, parsed with ``Decimal`` at the point of
    use.  It was a ``float`` in an earlier revision, and that was the last float left in a
    numeric decision path.  The threshold is compared against
    ``controls / len(text)`` to decide whether a node returns text or
    *raises* -- so a one-ulp difference between two CPython builds is not a
    rounding artifact, it is one validator raising ``NotTextual`` while
    another returns a document.  Making the field a string moves R3 from an
    argued property to a structural one: there is no binary float anywhere on
    the path, and the exact text of the threshold is what reaches
    ``policy_id``.

    Output-affecting in the harshest way (R6): below the threshold a node
    returns text, above it the node raises.  Two validators on different
    thresholds would disagree about whether the page is a document at all,
    with no visible signal -- unless the threshold is in ``policy_id``, which
    it is.
    """

    detect_bot_wall_server_hint: bool = False
    """Enable the ``Server:`` header + :data:`~webanchor.detect.CHALLENGE_SHAPE_MARKERS`
    branch of bot-wall detection in :func:`webanchor.detect.check_response`.

    Defaults to ``False``.  Measured on the benchmark corpus
    (``BENCHMARK.md``, "Cloudflare-branch finding"): with this branch enabled,
    2 of 5 small legitimate pages fronted by a challenge-capable edge
    (``Server: cloudflare``) raised :class:`~webanchor.errors.BotWallDetected`
    on ordinary phrases -- ``"please wait"``, ``"redirecting"`` -- that a
    landing stub or redirect page says routinely; on the one real bot-wall
    fixture in this repository, the strong-marker tier alone already fires,
    so the branch's measured marginal benefit on that corpus was zero. A
    40%-false-positive / 0%-marginal-benefit branch has no business running
    by default: a refused valid page is the same failure, facing the other
    way, as an accepted challenge page, and is worse in one respect --
    it silently costs availability with no compensating win. :meth:`Policy.strict`
    turns it on, consistent with ``strict`` already being the aggressive,
    know-your-page, opt-in policy; :meth:`Policy.default` leaves it off. Strong
    and weak markers are unaffected by this field and always run -- the data
    showed those two tiers alone already catch the real fixture.
    """

    def __post_init__(self) -> None:
        if self.number_band_mode not in _NUMBER_BAND_MODES:
            raise PolicyError(
                "number_band_mode must be one of {0}, got {1!r}".format(
                    list(_NUMBER_BAND_MODES), self.number_band_mode
                )
            )
        if self.unicode_form not in _UNICODE_FORMS:
            raise PolicyError(
                "unicode_form must be one of {0}, got {1!r}".format(
                    list(_UNICODE_FORMS), self.unicode_form
                )
            )
        if self.timestamp_mode not in _TIMESTAMP_MODES:
            raise PolicyError(
                "timestamp_mode must be one of {0}, got {1!r}".format(
                    list(_TIMESTAMP_MODES), self.timestamp_mode
                )
            )
        if self.timestamp_quantum_seconds < 1:
            raise PolicyError(
                "timestamp_quantum_seconds must be >= 1, got {0!r}".format(
                    self.timestamp_quantum_seconds
                )
            )
        if self.max_content_bytes < 1:
            raise PolicyError(
                "max_content_bytes must be >= 1, got {0!r}".format(
                    self.max_content_bytes
                )
            )
        if self.max_tag_depth < 1:
            raise PolicyError(
                "max_tag_depth must be >= 1, got {0!r}".format(self.max_tag_depth)
            )
        ratio = _parse_decimal_field(
            "max_control_char_ratio", self.max_control_char_ratio
        )
        if not Decimal(0) <= ratio <= Decimal(1):
            raise PolicyError(
                "max_control_char_ratio must be between 0 and 1 "
                "inclusive, got {0!r}".format(self.max_control_char_ratio)
            )
        step = _parse_decimal_field("number_grid_step", self.number_grid_step)
        if step <= 0:
            raise PolicyError(
                "number_grid_step must be > 0, got {0!r}".format(
                    self.number_grid_step
                )
            )
        if self.number_significant_digits < 1:
            raise PolicyError(
                "number_significant_digits must be >= 1, got {0!r}".format(
                    self.number_significant_digits
                )
            )
        if self.schema_version < 1:
            raise PolicyError(
                "schema_version must be >= 1, got {0!r}".format(self.schema_version)
            )
        object.__setattr__(self, "volatile_attrs", tuple(self.volatile_attrs))
        object.__setattr__(
            self, "ad_container_patterns", tuple(self.ad_container_patterns)
        )
        object.__setattr__(
            self,
            "extra_ad_container_patterns",
            tuple(self.extra_ad_container_patterns),
        )

    # -- derived views -----------------------------------------------------

    def control_char_ratio(self) -> Decimal:
        """:attr:`max_control_char_ratio` as an exact Decimal.

        Validated in :meth:`__post_init__`, so this cannot raise on a
        constructed Policy.
        """
        return Decimal(self.max_control_char_ratio)

    def grid_step(self) -> Decimal:
        """:attr:`number_grid_step` as an exact Decimal (validated ``> 0``)."""
        return Decimal(self.number_grid_step)

    def effective_ad_container_patterns(self) -> tuple[str, ...]:
        """The patterns actually used, as a deterministically ordered tuple.

        ``set(ad_container_patterns) | set(extra_ad_container_patterns)``,
        materialized **sorted**.  Returning the raw set would leak iteration
        order into anything that serializes or joins it, and set iteration
        order is not a stable contract across builds -- blueprint rule R3.
        """
        return tuple(
            sorted(set(self.ad_container_patterns) | set(self.extra_ad_container_patterns))
        )

    # -- constructors ------------------------------------------------------

    @classmethod
    def default(cls) -> "Policy":
        """The conservative policy: strip volatile DOM, keep every content node.

        Scripts, styles, comments and non-content subtrees go; ad-container
        heuristics and number banding stay off.  Timestamps are redacted,
        which is the only timestamp treatment with a total guarantee.  Nothing
        that could be prose is discarded.
        """
        return cls()

    @classmethod
    def strict(cls) -> "Policy":
        """Aggressive normalization for maximally convergent fingerprints.

        Turns ad-container stripping ON.  Use only when you accept the risk
        that a generic class name such as ``promo`` costs you real content.
        Also turns :attr:`detect_bot_wall_server_hint` ON -- the extra
        ``Server:``-assisted bot-wall branch, opt-in for the same reason:
        it is an aggressive heuristic with a measured false-positive cost
        (see ``BENCHMARK.md``), appropriate when you know the page and
        accept that trade, not as a silent default.
        """
        return cls(
            number_band_mode="significant",
            timestamp_quantum_seconds=86_400,
            lowercase=True,
            strip_ad_containers=True,
            detect_bot_wall_server_hint=True,
        )

    def with_changes(self, **changes: Any) -> "Policy":
        """Return a new validated Policy with the given fields replaced."""
        return replace(self, **changes)

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Primitive-only view (tuples become lists) used for id derivation."""
        return {
            "behavior_version": BEHAVIOR_VERSION,
            "schema_version": self.schema_version,
            "strip_scripts": self.strip_scripts,
            "strip_styles": self.strip_styles,
            "strip_comments": self.strip_comments,
            "strip_ad_containers": self.strip_ad_containers,
            "ad_container_patterns": list(self.ad_container_patterns),
            "extra_ad_container_patterns": list(self.extra_ad_container_patterns),
            "effective_ad_container_patterns": list(
                self.effective_ad_container_patterns()
            ),
            "volatile_attrs": list(self.volatile_attrs),
            "unicode_form": self.unicode_form,
            "collapse_whitespace": self.collapse_whitespace,
            "lowercase": self.lowercase,
            "number_band_mode": self.number_band_mode,
            "number_grid_step": self.number_grid_step,
            "number_significant_digits": self.number_significant_digits,
            "timestamp_mode": self.timestamp_mode,
            "timestamp_quantum_seconds": self.timestamp_quantum_seconds,
            "max_content_bytes": self.max_content_bytes,
            "max_tag_depth": self.max_tag_depth,
            "max_control_char_ratio": self.max_control_char_ratio,
            "detect_bot_wall_server_hint": self.detect_bot_wall_server_hint,
        }

    def canonical_json(self) -> str:
        """Byte-stable JSON encoding of :meth:`to_dict`."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @property
    def policy_id(self) -> str:
        """Deterministic identity: ``p1:`` + first 32 hex chars of sha256.

        Derived from :meth:`canonical_json` via ``hashlib.sha256`` -- never
        from the builtin ``hash()``, which is salted per process by
        ``PYTHONHASHSEED`` and would silently break consensus.

        The digest is truncated to 128 bits, not fewer.  A policy_id collision
        would let two validators run *different* normalization while agreeing
        on a fingerprint -- the exact silent divergence R5 exists to prevent --
        and a 64-bit id is findable by brute force in minutes.
        """
        digest = hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
        return POLICY_ID_PREFIX + digest[:POLICY_ID_HEX_LEN]



# ============================================================================
# --- from webanchor/timestamps.py ---
# ============================================================================

"""Timestamp detection, quantization, and redaction.

Timestamps are the purest form of the divergence problem.  The leader fetches
at 08:59:58 and the validator fetches at 09:00:03; the page says "Updated 2
minutes ago" to one and "Updated 3 minutes ago" to the other, or renders a
"generated at" stamp that ticks every second.  Nothing about the *fact* under
consensus changed.  The bytes changed anyway.

Two classes, handled differently
--------------------------------
**Relative expressions** ("2 hours ago", "just now", "yesterday") are always
volatile and are replaced with the fixed token ``[relative-time]`` in both
``redact`` and ``quantize`` mode.  They are deliberately **not** resolved to
absolute times.  Resolving requires a value for "now", "now" is different on
every validator, and a resolver would therefore manufacture divergence out of
a string that was merely useless.  Replacing is total: the output no longer
depends on when anyone fetched.

**Absolute timestamps** (ISO-8601, RFC-1123, and the common human forms) carry
real information and are treated per ``policy.timestamp_mode``:

* ``none`` -- untouched.
* ``quantize`` -- floored to ``policy.timestamp_quantum_seconds`` and
  re-rendered in one canonical form, ``YYYY-MM-DDTHH:MM:SSZ``.  A date with no
  time component floors to midnight UTC and is therefore already stable.
* ``redact`` (default) -- replaced with ``[timestamp]``.

Quantization does NOT eliminate divergence
------------------------------------------
This is the limitation to read twice, and it is stated here rather than
discovered in production.

Flooring to a bucket makes disagreement *rare*.  It does not make it
impossible.  Two fetches that straddle a bucket edge land in different
buckets and produce different text, different fingerprints, and a failed
consensus round.  A leader reading ``08:59:58`` and a validator reading
``09:00:03`` -- five seconds apart -- disagree under an hourly quantum just as
surely as if there had been no quantization at all.

    residual divergence risk ~= (spread between fetches) / quantum

with the spread measured as the wall-clock window across all validators for
the value in question.  A 5-second spread against a 3600-second quantum is
roughly a 1-in-720 failure rate per timestamp on the page; with ten
timestamps, roughly 1 in 72 rounds.  Enlarging the quantum lowers the rate
proportionally and never reaches zero.  The failure is also *not* independent
across validators in the way a naive reading suggests: a fetch storm clustered
near a bucket edge splits the validator set, which is the worst case, not the
average one.

``test_quantization_does_not_eliminate_boundary_straddle`` in
``tests/test_timestamps.py`` asserts this divergence directly.  It is a
feature of the test suite, not an oversight: a guarantee whose boundary is
untested is a guarantee nobody can rely on.

This is not a quirk of timestamps.  It is the shared property of *every*
bucketing scheme in WebAnchor -- quantization here, significant-digit
rounding and grid banding in :mod:`webanchor.numbers`.  The general statement,
including how the risk compounds and why the failures are correlated rather
than independent, is written once in :mod:`webanchor.behavior`, section
"Every bucketing scheme leaves residual divergence".

Why ``redact`` is the default
-----------------------------
Redaction is the only mode with a *total* guarantee: ``[timestamp]`` is the
same string no matter when anyone fetched, so the residual risk is exactly
zero rather than merely small.  And the trade is cheap, because a wall-clock
timestamp is almost never the evidence a contract actually needs -- the
contract wants the order status, the price, the vote count, the presence of a
statement.  "When was this page rendered" is page furniture.  Paying a
guaranteed consensus risk for furniture is a bad trade, so it is not the
default one.  Choose ``quantize`` when the timestamp is genuinely the
evidence, and size the quantum against your fetch spread with the formula
above.

Determinism notes
-----------------
* Naive timestamps (no timezone) are interpreted as **UTC**.  That is an
  assumption, but a fixed one -- every validator makes the same assumption, so
  it cannot cause divergence.  Reading the host's local zone would.
* Epoch seconds are computed by integer arithmetic on ``timedelta`` fields,
  never via ``datetime.timestamp()``, which returns a ``float``.
* Output is rendered by explicit integer formatting, never ``strftime``:
  ``strftime`` delegates to the platform C library, and its handling of years
  below 1000 and of zero padding is genuinely platform-dependent.
"""

import re
from datetime import datetime, timedelta, timezone


__all__ = [
    "quantize_timestamps",
    "RELATIVE_TIME_TOKEN",
    "TIMESTAMP_TOKEN",
    "RELATIVE_RE",
    "ABSOLUTE_RE",
    "CANONICAL_TIMESTAMP_RE",
]

#: Replacement for every relative expression, in redact and quantize alike.
RELATIVE_TIME_TOKEN = "[relative-time]"

#: Replacement for an absolute timestamp in redact mode.
TIMESTAMP_TOKEN = "[timestamp]"

#: The exact shape :func:`quantize_timestamps` emits in quantize mode.
#: Published so that later pipeline stages can recognize their predecessor's
#: output and leave it alone -- see :func:`webanchor.numbers.band_numbers`.
#: A quantized timestamp is nine digits in a trench coat, and the number
#: matcher would happily round ``2024-04-12T00:00:00Z`` into
#: ``2020-4-12T0:0:0Z`` if nobody told it not to.
CANONICAL_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", re.IGNORECASE
)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_MONTH_NAMES = (
    ("january", 1),
    ("february", 2),
    ("march", 3),
    ("april", 4),
    ("may", 5),
    ("june", 6),
    ("july", 7),
    ("august", 8),
    ("september", 9),
    ("october", 10),
    ("november", 11),
    ("december", 12),
)

#: Month name -> number, full names and three-letter abbreviations.  Built as
#: a sorted-key dict so that nothing downstream can depend on insertion order.
_MONTHS: dict[str, int] = {}
for _name, _number in _MONTH_NAMES:
    _MONTHS[_name] = _number
    _MONTHS[_name[:3]] = _number
_MONTHS["sept"] = 9

#: Longest-first alternation.  Regex alternation is first-match, not
#: longest-match, so ``jan|january`` would match "jan" and leave "uary"
#: stranded.  Sorting by descending length removes that class of bug.
_MONTH_ALT = "|".join(sorted(_MONTHS, key=lambda m: (-len(m), m)))

_UNIT_ALT = (
    "seconds|second|secs|sec|minutes|minute|mins|min|hours|hour|hrs|hr|"
    "days|day|weeks|week|months|month|years|year"
)

#: Relative expressions.  Scoped deliberately: every pattern here is
#: unambiguously a time expression in ordinary prose.  Bare "today" is
#: excluded on purpose -- it appears constantly as a non-timestamp word
#: ("today only", "today's pick") and replacing it would damage sentences the
#: LLM has to read, for no divergence benefit that "yesterday" does not
#: already illustrate.
RELATIVE_RE = re.compile(
    r"(?<![\w-])("
    r"just\s+now"
    r"|(?:a|one)\s+(?:moment|few\s+moments|second|minute|hour)s?\s+ago"
    r"|moments\s+ago"
    r"|\d+\s+(?:" + _UNIT_ALT + r")\s+ago"
    r"|an?\s+(?:" + _UNIT_ALT + r")\s+ago"
    r"|yesterday"
    r")(?![\w-])",
    re.IGNORECASE,
)

_TZ = r"(?:Z|z|[+-]\d{2}:?\d{2})"

#: Absolute timestamps, as one alternation so that a single left-to-right pass
#: cannot let one pattern consume a fragment another needed.  RFC-1123 comes
#: first because it *contains* a "12 Apr 2024" that the human-date pattern
#: would otherwise match, leaving a stray ", ... GMT" behind.
ABSOLUTE_RE = re.compile(
    r"(?P<rfc>"
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+(?P<rfc_d>\d{1,2})\s+"
    r"(?P<rfc_mo>" + _MONTH_ALT + r")\s+(?P<rfc_y>\d{4})\s+"
    r"(?P<rfc_h>\d{2}):(?P<rfc_mi>\d{2})(?::(?P<rfc_s>\d{2}))?\s+"
    r"(?:GMT|UTC)"
    r")"
    r"|(?P<iso>"
    r"(?<!\d)(?P<iso_y>\d{4})-(?P<iso_mo>\d{2})-(?P<iso_d>\d{2})"
    r"(?:[T ](?P<iso_h>\d{2}):(?P<iso_mi>\d{2})"
    r"(?::(?P<iso_s>\d{2}))?(?:\.\d+)?(?P<iso_tz>" + _TZ + r")?)?(?!\d)"
    r")"
    r"|(?P<mdy>"
    r"(?<![\w-])(?P<mdy_mo>" + _MONTH_ALT + r")\.?\s+(?P<mdy_d>\d{1,2})"
    r"(?:st|nd|rd|th)?,?\s+(?P<mdy_y>\d{4})(?!\d)"
    r")"
    r"|(?P<dmy>"
    r"(?<!\d)(?P<dmy_d>\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(?P<dmy_mo>" + _MONTH_ALT + r")\.?,?\s+(?P<dmy_y>\d{4})(?!\d)"
    r")",
    re.IGNORECASE,
)


def _int(value: "str | None") -> int:
    return int(value) if value else 0


def _offset_minutes(tz: "str | None") -> int:
    """Minutes to SUBTRACT from the local reading to reach UTC."""
    if not tz or tz in ("Z", "z"):
        return 0
    sign = -1 if tz[0] == "-" else 1
    body = tz[1:].replace(":", "")
    return sign * (int(body[:2]) * 60 + int(body[2:4]))


def _matched_datetime(match: "re.Match[str]") -> "datetime | None":
    """Build a tz-aware UTC datetime from a match, or ``None`` if invalid.

    Returning ``None`` rather than raising is correct here: ``2024-02-31`` is
    a string that *looks* like a date and is not one, and the honest response
    is to leave it in the text untouched, not to abort normalization of the
    whole page over a typo on a footer.
    """
    if match.group("rfc"):
        year, day = int(match.group("rfc_y")), int(match.group("rfc_d"))
        month = _MONTHS[match.group("rfc_mo").lower()]
        hour, minute = int(match.group("rfc_h")), int(match.group("rfc_mi"))
        second, offset = _int(match.group("rfc_s")), 0
    elif match.group("iso"):
        year = int(match.group("iso_y"))
        month = int(match.group("iso_mo"))
        day = int(match.group("iso_d"))
        hour = _int(match.group("iso_h"))
        minute = _int(match.group("iso_mi"))
        second = _int(match.group("iso_s"))
        offset = _offset_minutes(match.group("iso_tz"))
    elif match.group("mdy"):
        year, day = int(match.group("mdy_y")), int(match.group("mdy_d"))
        month = _MONTHS[match.group("mdy_mo").lower()]
        hour = minute = second = offset = 0
    else:
        year, day = int(match.group("dmy_y")), int(match.group("dmy_d"))
        month = _MONTHS[match.group("dmy_mo").lower()]
        hour = minute = second = offset = 0

    try:
        moment = datetime(
            year, month, day, hour, minute, second, tzinfo=timezone.utc
        )
    except ValueError:
        return None
    if offset:
        try:
            moment = moment - timedelta(minutes=offset)
        except OverflowError:
            return None
    return moment


def _epoch_seconds(moment: datetime) -> int:
    """Exact integer seconds since the epoch.

    ``datetime.timestamp()`` returns a ``float`` and is therefore banned from
    this codebase's numeric paths.  ``timedelta`` stores days/seconds/micro-
    seconds as integers with a normalized non-negative seconds field, so this
    arithmetic is exact and floors correctly on both sides of the epoch.
    """
    delta = moment - _EPOCH
    return delta.days * 86400 + delta.seconds


def _render_utc(moment: datetime) -> str:
    """Canonical rendering, by integer formatting rather than ``strftime``."""
    return "{0:04d}-{1:02d}-{2:02d}T{3:02d}:{4:02d}:{5:02d}Z".format(
        moment.year,
        moment.month,
        moment.day,
        moment.hour,
        moment.minute,
        moment.second,
    )


def quantize_timestamps(text: str, policy: Policy) -> str:
    """Redact or quantize every timestamp in ``text``.

    Relative expressions become :data:`RELATIVE_TIME_TOKEN` under both
    ``redact`` and ``quantize``; absolute timestamps become
    :data:`TIMESTAMP_TOKEN` under ``redact`` and a floored, canonically
    rendered UTC instant under ``quantize``.  ``none`` returns the text
    unchanged, relative expressions included.

    Read the module docstring before choosing ``quantize``: it reduces the
    divergence rate to roughly ``spread / quantum`` and does not eliminate it.
    """
    mode = policy.timestamp_mode
    if mode == "none":
        return text

    text = RELATIVE_RE.sub(RELATIVE_TIME_TOKEN, text)

    if mode == "redact":
        return ABSOLUTE_RE.sub(
            lambda m: TIMESTAMP_TOKEN
            if _matched_datetime(m) is not None
            else m.group(0),
            text,
        )

    quantum = policy.timestamp_quantum_seconds

    def substitute(match: "re.Match[str]") -> str:
        moment = _matched_datetime(match)
        if moment is None:
            return match.group(0)
        seconds = _epoch_seconds(moment)
        floored = seconds - (seconds % quantum)
        try:
            bucket = _EPOCH + timedelta(seconds=floored)
        except (OverflowError, OSError, ValueError):
            # Flooring pushed the instant outside the representable range.
            # Leaving the original text is the only non-lying option.
            return match.group(0)
        return _render_utc(bucket)

    return ABSOLUTE_RE.sub(substitute, text)



# ============================================================================
# --- from webanchor/text.py ---
# ============================================================================

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



# ============================================================================
# --- from webanchor/numbers.py ---
# ============================================================================

"""Number detection and banding, in exact decimal arithmetic.

Why ``decimal.Decimal`` and never ``float``
-------------------------------------------
This module is forbidden from touching binary floating point, and that is a
consensus requirement rather than a style preference.  ``float("0.1") * 3``
is ``0.30000000000000004``; ``repr`` of a float has changed across CPython
releases; ``round()`` on a float uses banker's rounding over a value that is
already the wrong value.  A leader and a validator on different CPython
builds, or on WASI versus x86-64, can render the same decimal input with a
different final digit -- and one differing digit is a different fingerprint.
``Decimal`` constructed from the matched *string* is exact, its arithmetic is
exact, and its arithmetic is exact, and its rounding mode is stated
explicitly rather than inherited from hardware.  ``policy.number_grid_step``
is itself a **string**, not a float, precisely so that it never has to make
the round trip through binary floating point at all.

The thousands-separator ambiguity rule
--------------------------------------
``1,234`` is 1234 in en-US and 1.234 in de-DE.  No amount of cleverness
resolves that from the substring alone, and *per-occurrence* heuristics
(guessing from surrounding text, from the other numbers on the page, from a
detected locale) are strictly forbidden here: they make the output depend on
inputs the validator may not have seen identically, which converts an
ambiguity into a divergence.  One fixed rule is applied to every number:

1. Space-class separators (SPACE, U+00A0, U+202F) are **always** thousands
   separators.  They are never a decimal point in any convention.
2. If the number contains **both** ``,`` and ``.``, the one that appears
   **last** is the decimal separator and the other is a thousands separator.
   ``1,234.56`` -> 1234.56 and ``1.234,56`` -> 1234.56.  This is
   convention-independent and never wrong.
3. If only one kind of ``,``/``.`` appears:

   a. more than once -> all occurrences are thousands separators
      (``1.234.567`` -> 1234567);
   b. exactly once, with **1 to 3 digits before it and exactly 3 after** ->
      **thousands separator**.  This is the ambiguous case, and this is the
      documented tie-break: ``1,234`` -> 1234 and ``1.234`` -> **1234**;
   c. otherwise -> decimal separator (``1.5``, ``0,75``, ``12345.678``).

Rule 3b is the one that can be "wrong": a genuine en-US ``1.234`` meaning one
point two three four is read as 1234.  That is accepted deliberately.  Three
digits after a separator is a thousands group far more often than it is a
three-decimal price, and -- decisively -- the rule is *fixed*, so every
validator is wrong in exactly the same way, which costs an approximate band
rather than consensus.

Grouping is validated: if any group treated as thousands is not exactly three
digits, the match is **left untouched**.  That is what keeps ``1.2.3`` and
``192.168.0.1`` out of the banding path entirely.

Grid banding, and why it replaced percent banding
-------------------------------------------------
``grid`` mode floors each value onto a fixed lattice of width
``policy.number_grid_step`` and renders the bucket it landed in::

    low  = floor(v / step) * step
    high = low + step
    token = "[low~high]"          rendered at the SCALE OF STEP

The lattice is shared: it depends only on ``step``, never on the value being
banded.  That is the whole point, and it is what the removed ``percent`` mode
got wrong.  ``percent`` centred an interval on *each validator's own
reading* -- 1000 became ``[900~1100]`` and 1050 became ``[945~1155]``.  Those
intervals overlap, so the mode *looked* like it expressed tolerance, but
``strict_eq`` compares strings and the strings differ.  A convergence feature
that does not converge is worse than no feature, so ``percent`` was removed
outright rather than deprecated.  Under ``grid`` with ``step="100"`` both
readings render ``[1000~1100]`` and the validators agree.

Grid inherits the residual-divergence property of every bucketing scheme in
this library: two readings that *straddle* a lattice edge still land in
different buckets and still diverge.  The property is stated once, generally,
in :mod:`webanchor.behavior` (section "Every bucketing scheme leaves residual
divergence"); read it there rather than assuming ``grid`` is a guarantee.
The short version: risk falls as ``spread / step`` and never reaches zero.

Idempotency
-----------
``significant`` mode is idempotent: rounding an already-rounded value to the
same number of significant digits is a no-op, and the canonical renderer
strips trailing fractional zeros so the rendering is a fixed point too.

``grid`` mode is **not** idempotent, and cannot be: ``1250`` becomes
``[1200~1300]``, and a second pass sees two fresh numbers inside the band
token.  Band a page once.  This is tested rather than papered over.
"""

import re
from decimal import ROUND_HALF_EVEN, Decimal


__all__ = ["band_numbers", "NUMBER_RE", "CURRENCY_SYMBOLS"]


#: Leading currency symbols recognized and preserved.  Kept deliberately
#: short: every symbol added here changes output and needs a BEHAVIOR_VERSION
#: bump, and a long tail of rare symbols buys little.
CURRENCY_SYMBOLS = "$€£¥"

_SPACE_SEPS = "   "

#: One number, with its optional currency prefix and percent suffix.
#:
#: The digit core is ``\d+(?:[.,]\d+|[space]\d{3}(?!\d))*``.  Space-class
#: separators are only accepted before **exactly three** digits, which is what
#: stops ``42 3`` in ``Widget 42 3`` from being read as 423 while still
#: accepting the ``1 234,56`` form the spec requires.  ``,``/``.`` groups are
#: accepted with any digit count here and validated during parsing, so that
#: ``1.5`` and ``1.234.567`` both reach the parser and ``1.2.3`` is rejected
#: there rather than by a more fragile pattern.
NUMBER_RE = re.compile(
    r"(?P<currency>[" + CURRENCY_SYMBOLS + r"][" + _SPACE_SEPS + r"]?)?"
    r"(?P<sign>[+-])?"
    r"(?P<core>\d+(?:[.,]\d+|[" + _SPACE_SEPS + r"]\d{3}(?!\d))*)"
    r"(?P<percent>[" + _SPACE_SEPS + r"]?%)?"
)

_SPLIT_RE = re.compile(r"([.,%s])" % _SPACE_SEPS)


class _Unparseable(Exception):
    """Internal signal: this match is not a well-formed grouped number."""


def _parse_decimal(core: str) -> Decimal:
    """Apply the documented ambiguity rule and return an exact Decimal.

    Raises :class:`_Unparseable` when the separator layout is not a valid
    grouped number, in which case the caller leaves the text alone.
    """
    pieces = _SPLIT_RE.split(core)
    groups = pieces[0::2]
    seps = pieces[1::2]

    if not seps:
        return Decimal(groups[0])

    # Rule 1: space-class separators are always thousands separators.
    punct_positions = [i for i, s in enumerate(seps) if s in (",", ".")]
    punct_kinds = {seps[i] for i in punct_positions}

    decimal_index = -1
    if len(punct_kinds) == 2:
        # Rule 2: whichever of , and . comes last is the decimal separator.
        decimal_index = punct_positions[-1]
    elif len(punct_kinds) == 1:
        if len(punct_positions) == 1:
            index = punct_positions[0]
            before = groups[index]
            after = groups[index + 1]
            # Rule 3b vs 3c.
            if len(before) <= 3 and len(after) == 3:
                decimal_index = -1  # thousands
            else:
                decimal_index = index
        else:
            decimal_index = -1  # Rule 3a: repeated -> all thousands

    # Every separator that is not the decimal one must delimit a 3-digit
    # group, and the leading group must be 1-3 digits.  Otherwise this is a
    # version string or an IP address, not a number we may rewrite.
    integer_groups = groups if decimal_index < 0 else groups[: decimal_index + 1]
    if len(integer_groups) > 1:
        if not 1 <= len(integer_groups[0]) <= 3:
            raise _Unparseable(core)
        for group in integer_groups[1:]:
            if len(group) != 3:
                raise _Unparseable(core)

    integer_part = "".join(integer_groups)
    if decimal_index < 0:
        return Decimal(integer_part)
    fraction = groups[decimal_index + 1]
    return Decimal(integer_part + "." + fraction)


def render_decimal(value: Decimal) -> str:
    """Canonical, exponent-free, trailing-zero-free rendering of a Decimal.

    ``format(value, "f")`` is used rather than ``str``: ``str`` emits
    scientific notation for values ``quantize`` produced at a positive
    exponent (``Decimal("1.23E+3")``), and ``"1.23E+3"`` in prose text is both
    unreadable to an LLM and a needless second representation of ``1230``.

    Trailing fractional zeros are stripped so that rendering is a fixed point:
    without that, ``1.230`` re-banded to three significant digits would render
    ``1.23`` and the second pass would disagree with the first.
    """
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-"):
        text = "0"
    if text == "-0":
        text = "0"
    return text


def _round_significant(value: Decimal, digits: int) -> Decimal:
    """Round to ``digits`` significant digits with an explicit rounding mode.

    ``ROUND_HALF_EVEN`` is named explicitly rather than left to the ambient
    decimal context: the context is process-global mutable state, and a
    validator that imported a library which changed it would round
    differently from the leader.
    """
    if value == 0:
        return Decimal(0)
    exponent = value.adjusted() - (digits - 1)
    return value.quantize(Decimal(1).scaleb(exponent), rounding=ROUND_HALF_EVEN)


def _scaled_int(value: Decimal, exponent: int) -> int:
    """``value`` expressed as an exact integer of ``10 ** exponent`` units.

    Requires ``exponent <= value.as_tuple().exponent`` so that no digit is
    ever discarded.  Built from the Decimal's own digit tuple with integer
    arithmetic rather than by division or ``scaleb``: those route through the
    process-global decimal context, whose precision another library is free to
    change, and a silently rounded bucket edge is a silently wrong band.
    """
    sign, digits, own_exponent = value.as_tuple()
    assert isinstance(own_exponent, int) and own_exponent >= exponent
    magnitude = 0
    for digit in digits:
        magnitude = magnitude * 10 + digit
    magnitude *= 10 ** (own_exponent - exponent)
    return -magnitude if sign else magnitude


def _fixed_string(units: int, decimals: int) -> str:
    """Render an integer count of ``10 ** -decimals`` units in plain notation.

    Pure integer string surgery: no float, no ``format``, no locale, and no
    exponent notation for any magnitude.  ``format(Decimal, "f")`` would do
    for the values seen in practice, but it consults the decimal context for
    very large inputs and this function must not have a size limit.
    """
    negative = units < 0
    text = str(-units if negative else units)
    if decimals:
        text = text.rjust(decimals + 1, "0")
        text = text[: len(text) - decimals] + "." + text[len(text) - decimals :]
    if negative and set(text) - {"0", "."}:
        text = "-" + text
    return text


def _restate(units: int, source_exponent: int, target_exponent: int) -> int:
    """Re-express ``units * 10**source`` as a count of ``10**target`` units.

    Exact in both directions.  Scaling *down* is only ever asked of a value
    that is an exact multiple of the step, so the division has no remainder;
    the assertion says so out loud rather than letting a silent truncation
    produce a plausible-looking wrong band.
    """
    if source_exponent >= target_exponent:
        return units * 10 ** (source_exponent - target_exponent)
    divisor = 10 ** (target_exponent - source_exponent)
    assert units % divisor == 0
    return units // divisor


def _grid_band(value: Decimal, step: Decimal) -> str:
    """Render the ``[low~high]`` lattice bucket that contains ``value``.

    ``low = floor(value / step) * step`` and ``high = low + step``, computed in
    exact integer arithmetic on a common exponent so that the floor is a true
    floor for negative values too (Python's ``//`` floors; ``Decimal``'s
    truncates toward zero, which would put ``-0.5`` in the wrong bucket).

    Both endpoints are rendered at **the scale of the step**, not at the scale
    of the value.  That is what makes the token a property of the lattice
    rather than of the reading: with ``step="0.5"`` every band prints one
    decimal place, so a validator that read ``1.20`` and one that read ``1.2``
    emit the identical string.  Deriving the scale from the value instead
    would reintroduce the divergence the mode exists to remove.
    """
    step_exponent = step.as_tuple().exponent
    assert isinstance(step_exponent, int)
    value_exponent = value.as_tuple().exponent
    assert isinstance(value_exponent, int)

    common = min(step_exponent, value_exponent)
    value_units = _scaled_int(value, common)
    step_units = _scaled_int(step, common)

    bucket = value_units // step_units  # floor division, negatives included
    low_units = bucket * step_units
    high_units = low_units + step_units

    # Endpoints are exact multiples of the step, so they are representable at
    # the step's own scale with nothing to round away.
    decimals = -step_exponent if step_exponent < 0 else 0
    target = -decimals
    return "[{0}~{1}]".format(
        _fixed_string(_restate(low_units, common, target), decimals),
        _fixed_string(_restate(high_units, common, target), decimals),
    )


def band_numbers(text: str, policy: Policy) -> tuple[str, dict[str, str]]:
    """Rewrite numbers in ``text`` per ``policy.number_band_mode``.

    Returns the rewritten text and a ``bands`` mapping from the **original
    matched substring** (currency symbol and percent sign included) to its
    replacement, suitable for :attr:`webanchor.Evidence.bands`.  That mapping
    is the audit trail: a contract can show exactly which literals were
    transformed, instead of having to trust that the pipeline did something
    reasonable.

    Modes:

    * ``none`` -- text returned unchanged, bands empty.  No detection is even
      attempted, so this mode cannot mangle anything.
    * ``significant`` -- each value is rounded to
      ``policy.number_significant_digits`` significant digits
      (``ROUND_HALF_EVEN``) and re-rendered canonically.  Collapses the
      last-digit jitter of counters, ratings and computed prices.  This is a
      bucketing scheme like the others and carries the same residual risk at
      bucket edges; see :mod:`webanchor.behavior`.
    * ``grid`` -- each value is replaced by the closed lattice bucket
      ``[low~high]`` that contains it, with ``low = floor(v / step) * step``
      and ``high = low + step`` for ``step = policy.number_grid_step``.  The
      lattice is shared by every validator, so two readings inside one step
      produce the *identical* string -- which is what the removed ``percent``
      mode failed to do.  The token is deliberately readable: it is going into
      an LLM prompt, and ``[1200~1300]`` tells the model what it is looking at
      while an opaque sigil does not.  Residual divergence at bucket edges is
      documented in :mod:`webanchor.behavior`.

    Digits inside a canonical quantized timestamp
    (``2024-04-12T00:00:00Z``) are protected and never rewritten: the pipeline
    runs timestamp handling first, and banding its output would undo it.

    A leading currency symbol and a trailing percent sign are preserved
    verbatim around the replacement, because dropping them changes the meaning
    of the sentence the LLM reads.  Matches whose separator layout is not a
    valid grouped number (``1.2.3``, ``192.168.0.1``) are left untouched.
    """
    mode = policy.number_band_mode
    if mode == "none":
        return text, {}

    # Spans the number matcher must not touch.  In the pipeline, timestamp
    # handling runs first, and in quantize mode it leaves behind
    # ``2024-04-12T00:00:00Z`` -- nine digits that the matcher would otherwise
    # round into ``2020-4-12T0:0:0Z``, destroying the timestamp the previous
    # stage just carefully canonicalized.  Ordering alone does not fix this:
    # the hazard is the *output* of the earlier stage, not its input.  The
    # redaction token needs no protection (it has no digits), but it is listed
    # for symmetry so that changing it cannot silently create the bug.
    protected = [m.span() for m in CANONICAL_TIMESTAMP_RE.finditer(text)]
    protected.extend(
        m.span() for m in re.finditer(re.escape(TIMESTAMP_TOKEN), text)
    )

    digits = policy.number_significant_digits
    step = policy.grid_step()
    bands: dict[str, str] = {}

    def is_protected(start: int, end: int) -> bool:
        for low, high in protected:
            if start < high and low < end:
                return True
        return False

    def substitute(match: "re.Match[str]") -> str:
        if is_protected(match.start(), match.end()):
            return match.group(0)
        original = match.group(0)
        known = bands.get(original)
        if known is not None:
            return known
        try:
            value = _parse_decimal(match.group("core"))
        except _Unparseable:
            return original
        if match.group("sign") == "-":
            value = -value
        if mode == "significant":
            token = render_decimal(_round_significant(value, digits))
        else:
            token = _grid_band(value, step)
        currency = match.group("currency")
        if currency:
            # Normalize any separator between symbol and digits away: whether
            # a template emitted "$ 5" or "$5" must not change the output.
            token = currency[0] + token
        suffix = match.group("percent")
        if suffix:
            token = token + "%"
        bands[original] = token
        return token

    return NUMBER_RE.sub(substitute, text), bands



# ============================================================================
# --- from webanchor/html_strip.py ---
# ============================================================================

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



# ============================================================================
# --- from webanchor/detect.py ---
# ============================================================================

"""Responses that must never reach the LLM.

This module is the "fails loudly instead of producing a verdict from a 429
page" half of the WebAnchor thesis.  Everything else in the library makes a
*good* response comparable; this makes a *bad* response impossible to mistake
for a good one.

Why status checking alone is not enough
---------------------------------------
The obvious defense is to look at the HTTP status and stop on 4xx/5xx.  It is
necessary and it is nowhere near sufficient, because the two most common ways
a web read goes wrong in practice both arrive as **HTTP 200**:

* A **bot wall / challenge**.  Cloudflare's interstitial, Incapsula's block
  page, an hCaptcha gate: these are ``200 OK`` with a body that says "Just a
  moment..." and a spinner.  A contract that hands that to an LLM and asks
  "is the order shipped?" gets an answer.  The answer is derived from a
  challenge page.  Nothing in the pipeline downstream of here can tell.
* A **soft error page**.  A CMS that renders its 404 template with a ``200``
  status, a maintenance page, a "service temporarily unavailable" splash.
  Same failure, different wrapper.

Both are *worse* than a hard error, because a hard error at least raises
somewhere.  These produce a plausible-looking verdict from content that has
nothing to do with the question, and they do it identically on every
validator -- so consensus **succeeds**, and the contract commits a wrong fact
with full agreement.  That is the single most expensive failure this library
can prevent, which is why detection lives in the default path rather than
behind a flag.

The false-positive budget is not zero
-------------------------------------
A library that refuses valid pages is unusable, and "refuses valid pages" is
not a smaller bug than "accepts challenge pages" -- it is the same bug facing
the other way.  A news article *about* Cloudflare outages legitimately
contains the string "checking your browser".  A blog post about HTTP status
codes legitimately contains "404" and "page not found".  Neither may raise.

Markers are therefore split into two tiers, and the tiering is the whole
design:

* :data:`BOT_WALL_STRONG_MARKERS` -- strings that are *machinery*, not prose:
  a Cloudflare challenge script path, an Incapsula resource name, a widget
  container id.  Their presence is dispositive at any body length, because no
  article writes ``cf-browser-verification`` in a sentence.
* :data:`BOT_WALL_WEAK_MARKERS` -- English phrases a challenge page shows a
  human.  Any of them can appear in real prose, so they fire **only** on a
  body whose *visible text* is short enough to be a challenge page and not an
  article.  See :data:`BOT_WALL_MAX_VISIBLE_CHARS`.

Soft-error detection is weak-tier only, and requires **two** signals: a short
visible body *and* an error phrase in the head region (the ``<title>`` or the
first :data:`SOFT_ERROR_HEAD_CHARS` characters of visible text).  A long
article that mentions 404 fails both conditions.

The ``Server:``+shape branch is opt-in, and here is the data that made it so
--------------------------------------------------------------------------
There is a third, narrower tier: when ``policy.detect_bot_wall_server_hint``
is ``True``, a short visible body on a ``Server:`` header naming a
challenge-capable edge (:data:`CHALLENGE_SERVER_TOKENS`) is checked against
:data:`CHALLENGE_SHAPE_MARKERS` -- phrases like ``"please wait"`` or
``"redirecting"`` that are challenge-shaped on their own but common in
ordinary small pages.

This tier defaults to **off** (``Policy.default().detect_bot_wall_server_hint
is False``; only :meth:`webanchor.Policy.strict` turns it on), and the reason
is measured, not argued: the benchmark corpus (``BENCHMARK.md``,
"Cloudflare-branch finding") ran this branch against
``tests/fixtures/corpus/small_legit/`` -- five tiny, realistic non-challenge
pages (a landing stub, a redirect stub, a status endpoint, a "coming soon"
page, a minimal FAQ) -- fronted by a synthetic ``Server: cloudflare`` header.
**2 of 5** raised :class:`~webanchor.errors.BotWallDetected`:
``coming_soon.html`` on ``"please wait"``, ``redirect_stub.html`` on
``"redirecting"``. Against the same benchmark's one real bot-wall fixture,
``tests/fixtures/cloudflare_challenge.html``, the strong-marker tier alone
already fires (``cf-browser-verification``) -- this branch was never the
only signal that caught it. Measured: a 40% false-positive rate on that
corpus for a 0% measured marginal benefit. That is the textbook case for
opt-in rather than default-on, and :meth:`webanchor.Policy.strict` -- the
policy for callers who know their page and accept aggressive-heuristic
trade-offs -- is where it lives.

Every raise names the marker that fired
---------------------------------------
:class:`~webanchor.errors.BotWallDetected` and
:class:`~webanchor.errors.SoftErrorPage` always carry the specific marker in
``detail``.  A typed error that does not say *why* it fired is only half
useful: the contract author cannot tell a genuine bot wall from a
false positive, cannot tune, and cannot report the bug.  Marker ordering
inside each tuple is therefore load-bearing -- first match wins, so the same
page always names the same marker on every validator.

R3 and R6
---------
Every marker list is a **tuple**, never a set: set iteration order is not a
stable contract, and a detail string that names a different marker on a
different build is a divergence in the error path.

Every marker list and every threshold here is output-affecting in the harshest
sense -- adding one marker flips some page from "returns Evidence" to
"raises".  They are covered by :data:`webanchor.behavior.BEHAVIOR_VERSION`,
which is folded into ``policy_id`` and therefore into every fingerprint.
**Adding, removing or reordering a marker requires a BEHAVIOR_VERSION bump.**
See the bump checklist in :mod:`webanchor.behavior`, item 11.
"""

import re
from typing import Mapping, Optional


__all__ = [
    "check_response",
    "visible_text",
    "BOT_WALL_STRONG_MARKERS",
    "BOT_WALL_WEAK_MARKERS",
    "CHALLENGE_SERVER_TOKENS",
    "CHALLENGE_SHAPE_MARKERS",
    "SOFT_ERROR_PHRASES",
    "BOT_WALL_MAX_VISIBLE_CHARS",
    "SOFT_ERROR_MAX_VISIBLE_CHARS",
    "SOFT_ERROR_HEAD_CHARS",
]


# ---------------------------------------------------------------------------
# Marker tables.  Tuples, not sets (R3).  Covered by BEHAVIOR_VERSION (R6).
# ---------------------------------------------------------------------------

#: Machinery markers.  Dispositive at any body length.
#:
#: Every entry is a literal that a challenge platform emits into the DOM and
#: that English prose does not contain: a script path, a resource name, a
#: cookie or query-parameter name, a widget container id, a vendor page title.
#: Lowercase; matched against a lowercased body.  Order is match priority.
BOT_WALL_STRONG_MARKERS: tuple[str, ...] = (
    "attention required! | cloudflare",
    "cf-browser-verification",
    "/cdn-cgi/challenge-platform",
    "__cf_chl",
    "cf_chl_opt",
    "cf-challenge-running",
    "request unsuccessful. incapsula incident id",
    "_incapsula_resource",
    "g-recaptcha",
    "h-captcha",
    "hcaptcha.com/captcha",
    "www.google.com/recaptcha/api.js",
    "px-captcha",
    "distil_r_captcha",
    "perimeterx",
    "datadome",
)

#: Human-facing challenge phrases.  Fire ONLY on a short visible body.
#:
#: Each of these can legitimately appear in an article, which is why length
#: gating is mandatory rather than a refinement.  Lowercase; matched against
#: lowercased *visible text*, not raw markup -- a phrase buried in a script
#: string on an otherwise normal page is not a challenge.
BOT_WALL_WEAK_MARKERS: tuple[str, ...] = (
    "just a moment...",
    "checking your browser",
    "checking if the site connection is secure",
    "please enable javascript and cookies",
    "enable javascript and cookies to continue",
    "verify you are human",
    "verifying you are human",
    "please verify you are a human",
    "complete the security check",
    "ddos protection by",
    "access denied",
    "you have been blocked",
    "unusual traffic from your computer network",
    "recaptcha",
    "hcaptcha",
    "captcha",
)

#: ``Server:`` header values that identify a challenge-capable edge.
#:
#: A match is NOT on its own evidence of anything -- a large share of the
#: legitimate web is behind Cloudflare.  It only lowers the bar for the weak
#: tier, and only in combination with a short, challenge-shaped body.
CHALLENGE_SERVER_TOKENS: tuple[str, ...] = (
    "cloudflare",
    "incapsula",
    "akamaighost",
)

#: Body shapes that, on a challenge-capable edge with a short body, indicate a
#: challenge rather than a small real page.
CHALLENGE_SHAPE_MARKERS: tuple[str, ...] = (
    "enable javascript",
    "enable cookies",
    "ray id",
    "security of your connection",
    "review the security of your connection",
    "needs to review the security",
    "please wait",
    "one more step",
    "redirecting",
)

#: Error phrasing that, in the head region of a SHORT page, means the 200 is
#: a lie.  Ordered most-specific first so the detail names the useful marker.
SOFT_ERROR_PHRASES: tuple[str, ...] = (
    "page not found",
    "404 not found",
    "error 404",
    "404 error",
    "not found",
    "no longer exists",
    "this page doesn't exist",
    "this page does not exist",
    "we'll be back",
    "we will be back",
    "temporarily unavailable",
    "service unavailable",
    "under maintenance",
    "site maintenance",
    "something went wrong",
    "an error occurred",
    "404",
    "error",
)

#: Above this many characters of *visible text*, a weak bot-wall marker is
#: treated as prose.  A Cloudflare interstitial renders a few hundred
#: characters; an article that mentions one runs to thousands.
BOT_WALL_MAX_VISIBLE_CHARS = 1200

#: Above this many characters of visible text, error phrasing is treated as
#: an article *about* errors.  Tighter than the bot-wall budget because the
#: phrases are far more common in ordinary prose.
SOFT_ERROR_MAX_VISIBLE_CHARS = 600

#: How much of the visible text counts as "the head".  A real error page leads
#: with the error; an article that mentions one buries it in the body.
SOFT_ERROR_HEAD_CHARS = 200


_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)


def visible_text(body: str) -> str:
    """A cheap, total approximation of what a reader would see.

    Deliberately *not* :func:`webanchor.html_strip.strip_html`.  The stripper
    raises on empty, oversized and non-textual input, and detection has to run
    **before** those checks -- a challenge page that also happens to be empty
    must be reported as a challenge, not as an empty page, because the two
    call for different actions from the contract author.  This function
    therefore never raises and never rejects: it drops script/style/noscript
    bodies and comments, deletes remaining tags, and collapses whitespace.

    It is used only for *length and phrase* judgements, never for anything
    that reaches a fingerprint, so its approximations cost nothing downstream.
    """
    text = _COMMENT_RE.sub(" ", body)
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _title_text(body: str) -> str:
    match = _TITLE_RE.search(body)
    if match is None:
        return ""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", match.group(1))).strip()


def _server_token(headers: Mapping[str, str]) -> Optional[str]:
    """The challenge-capable edge named by ``Server:``, if any.

    Header names are case-insensitive per RFC 9110, and the mapping handed to
    us may come from anywhere, so the lookup is done by lowercasing keys
    rather than by assuming a canonical spelling.  Keys are visited in the
    mapping's own order and the first ``server`` wins; a well-formed response
    has exactly one.
    """
    for key, value in headers.items():
        if key.lower() == "server":
            lowered = str(value).lower()
            for token in CHALLENGE_SERVER_TOKENS:
                if token in lowered:
                    return token
            return None
    return None


def _check_bot_wall(
    headers: Mapping[str, str],
    lowered_body: str,
    lowered_visible: str,
    url: Optional[str],
    policy: Policy,
) -> None:
    """Raise :class:`BotWallDetected` naming the first marker that fires."""
    for marker in BOT_WALL_STRONG_MARKERS:
        if marker in lowered_body:
            raise BotWallDetected(
                "challenge marker {0!r} found in the response body; this is a "
                "bot wall served as HTTP 200, not the page".format(marker),
                url=url,
            )

    visible_length = len(lowered_visible)
    if visible_length > BOT_WALL_MAX_VISIBLE_CHARS:
        # Long enough to be an article. Weak markers are prose here, and this
        # early return is what keeps a piece *about* bot walls readable.
        return

    for marker in BOT_WALL_WEAK_MARKERS:
        if marker in lowered_visible:
            raise BotWallDetected(
                "challenge phrase {0!r} found in a {1}-character body; a page "
                "this short saying this is a bot wall, not content".format(
                    marker, visible_length
                ),
                url=url,
            )

    if not policy.detect_bot_wall_server_hint:
        # Opt-in only (Policy.strict() turns it on). Measured on the benchmark
        # benchmark corpus (BENCHMARK.md, "Cloudflare-branch finding"): 2/5
        # small legitimate pages fronted by a challenge-capable edge raised
        # BotWallDetected on ordinary phrases ("please wait", "redirecting"),
        # while the strong- and weak-marker tiers above already caught the
        # one real bot-wall fixture in this repository without it -- a
        # measured 40% false-positive rate for 0% measured marginal benefit.
        return

    server = _server_token(headers)
    if server is None:
        return
    for marker in CHALLENGE_SHAPE_MARKERS:
        if marker in lowered_visible:
            raise BotWallDetected(
                "Server: {0} served a {1}-character body containing {2!r}; "
                "this is challenge-shaped, not content".format(
                    server, visible_length, marker
                ),
                url=url,
            )


def _check_soft_error(
    body_text: str, visible: str, url: Optional[str]
) -> None:
    """Raise :class:`SoftErrorPage` only when TWO independent signals agree.

    Signal one: the visible text is shorter than
    :data:`SOFT_ERROR_MAX_VISIBLE_CHARS`.  Signal two: an error phrase appears
    in the ``<title>`` or in the first :data:`SOFT_ERROR_HEAD_CHARS`
    characters of visible text.

    Requiring both is the entire defense against eating a legitimate article
    that discusses HTTP errors: such an article fails the length test, and
    even a short one usually buries the phrase past the head.  Conservatism is
    the correct bias here -- a wrongly-refused page is a visible, debuggable
    failure, but only if the library is still trusted enough to be used.
    """
    if len(visible) > SOFT_ERROR_MAX_VISIBLE_CHARS:
        return

    title = _title_text(body_text).lower()
    head = visible[:SOFT_ERROR_HEAD_CHARS].lower()

    for phrase in SOFT_ERROR_PHRASES:
        where = None
        if phrase in title:
            where = "the page title"
        elif phrase in head:
            where = "the first {0} characters".format(SOFT_ERROR_HEAD_CHARS)
        if where is None:
            continue
        raise SoftErrorPage(
            "error phrase {0!r} in {1} of a {2}-character page; this is an "
            "error page served as HTTP 200, not content".format(
                phrase, where, len(visible)
            ),
            url=url,
        )


def check_response(
    status: int,
    headers: Mapping[str, str],
    body_text: str,
    policy: Policy,
    *,
    url: Optional[str] = None,
) -> None:
    """Raise if this response must not reach the LLM; return ``None`` if usable.

    Returning ``None`` is the success signal.  There is no boolean and no
    result object on purpose: a caller cannot forget to check an exception
    the way it can forget to check a flag, and blueprint rule R4 wants the
    failure path loud.

    Checks run in this fixed order, and the order is the contract -- the
    *first* thing wrong with a response is what gets reported, so two
    validators looking at the same bytes raise the same error with the same
    detail:

    1. **Status.** Delegated to :func:`webanchor.errors.from_status`, which
       owns the status taxonomy; duplicating the mapping here would let the
       two drift.
    2. **Bot wall / challenge**, on an otherwise-fine 2xx.  Before the soft
       error check, because a challenge page often says "Access denied" and
       "challenge" is the more actionable diagnosis than "error page".
    3. **Soft error page**, on an otherwise-fine 2xx.
    4. **Textuality** -- NUL bytes or too many C0 controls, per
       ``policy.max_control_char_ratio``.  After the page-kind checks because
       "this is a bot wall" is more useful than "this is not text", and a
       mis-decoded challenge page can be both.
    5. **Size** -- over ``policy.max_content_bytes``.
    6. **Emptiness** -- empty or whitespace-only.  Last: an empty body that is
       *also* a challenge should be reported as the challenge.

    Args:
        status: HTTP status. In ``render`` fetch mode the SDK does not expose
            one and the caller passes ``200``; see :mod:`webanchor.fetch`.
            That is precisely why checks 2 and 3 exist.
        headers: Response headers, matched case-insensitively. May be empty --
            it always is in render mode, which weakens only the
            ``Server:``-assisted branch, not the body-based ones.
        body_text: The response body as text, **before** normalization.
            Markers live in markup (``cf-browser-verification`` is a class
            name), so stripping first would destroy the evidence.
        policy: Supplies the textuality and size limits, and reaches
            ``policy_id`` so two validators cannot silently apply different
            ones. Also gates the ``Server:``+shape-marker branch via
            ``policy.detect_bot_wall_server_hint`` -- off by
            :meth:`~webanchor.Policy.default`, on by
            :meth:`~webanchor.Policy.strict`; see the module docstring.
        url: Attached to every raised error for diagnosis.

    Raises:
        RateLimited, NotFound, Forbidden, UpstreamUnavailable,
        UnexpectedStatus: from the status mapping.
        BotWallDetected: a challenge or interstitial, naming the marker.
        SoftErrorPage: an error page served as 200, naming the phrase.
        NotTextual: NUL bytes, or C0 controls over the policy ratio.
        ContentTooLarge: body over ``policy.max_content_bytes``.
        EmptyContent: body empty or whitespace-only.
    """
    status_error = from_status(status, url=url)
    if status_error is not None:
        raise status_error

    visible = visible_text(body_text)
    lowered_body = body_text.lower()
    lowered_visible = visible.lower()

    _check_bot_wall(headers, lowered_body, lowered_visible, url, policy)
    _check_soft_error(body_text, visible, url)

    reject_non_textual(body_text, policy, url=url)

    size = len(body_text.encode("utf-8"))
    if size > policy.max_content_bytes:
        raise ContentTooLarge(
            "response is {0} bytes, over the {1}-byte policy limit".format(
                size, policy.max_content_bytes
            ),
            url=url,
        )

    if not body_text.strip():
        raise EmptyContent(
            "response body is empty or whitespace-only; there is no evidence "
            "in it to anchor",
            url=url,
        )



# ============================================================================
# --- from webanchor/fingerprint.py ---
# ============================================================================

"""The stable content hash that ``strict_eq`` actually compares."""

import hashlib
import hmac


__all__ = ["FINGERPRINT_VERSION", "fingerprint", "verify"]

FINGERPRINT_VERSION = "wa1"

_POLICY_ID_PREFIX = "p1:"


def _check_inputs(normalized_text: str, policy_id: str) -> None:
    if not policy_id.startswith(_POLICY_ID_PREFIX):
        raise PolicyMismatch(
            "policy_id must start with {0!r}, got {1!r}".format(
                _POLICY_ID_PREFIX, policy_id
            )
        )
    if not normalized_text or not normalized_text.strip():
        raise EmptyContent("cannot fingerprint empty or whitespace-only content")


def fingerprint(normalized_text: str, policy_id: str) -> str:
    """Return ``wa1:<sha256 hex>`` over the policy id and normalized text.

    The NUL byte between the two inputs is domain separation: without it,
    a policy id ending in one character and text starting with another could
    collide with a different (policy, text) split.
    """
    _check_inputs(normalized_text, policy_id)
    payload = policy_id.encode("utf-8") + b"\x00" + normalized_text.encode("utf-8")
    return "{0}:{1}".format(FINGERPRINT_VERSION, hashlib.sha256(payload).hexdigest())


def verify(normalized_text: str, policy_id: str, expected: str) -> bool:
    """Constant-time check that ``expected`` matches the recomputed fingerprint.

    A WebAnchor fingerprint is a CHECKSUM, not an authentication tag.

    A match proves exactly one thing: that the same normalized text and the
    same ``policy_id`` were hashed.  It proves nothing about *who* produced
    the value -- there is no key and no signature here, and every input is
    public.  In particular, it is not a defense against a malicious validator
    that runs the pipeline honestly over content which was itself manipulated
    upstream: garbage in, perfectly-matching fingerprints out.

    ``hmac.compare_digest`` is used so that no future refactor reintroduces a
    naive ``==`` comparison, not because there is a secret to protect.  Do not
    treat a verified fingerprint as a claim of authenticity or provenance.
    """
    actual = fingerprint(normalized_text, policy_id)
    return hmac.compare_digest(actual, expected)



# ============================================================================
# --- from webanchor/evidence.py ---
# ============================================================================

"""Evidence: the calldata-safe result object handed back to a contract."""

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

__all__ = ["Evidence"]


@dataclass(frozen=True, eq=True, init=False)
class Evidence:
    """An anchored web read.

    ``fingerprint`` is what validators compare; ``text`` is what the LLM reads.
    The two are kept separate on purpose -- shipping full page text through
    consensus calldata would bloat every transaction for no added guarantee.
    """

    url: str
    status: int
    fingerprint: str
    policy_id: str
    text: str
    fetched_bucket: int
    _bands: dict[str, str] = field(repr=False)

    def __init__(
        self,
        url: str,
        status: int,
        fingerprint: str,
        policy_id: str,
        text: str,
        bands: Optional[Mapping[str, str]] = None,
        fetched_bucket: int = 0,
    ) -> None:
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "fingerprint", fingerprint)
        object.__setattr__(self, "policy_id", policy_id)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "fetched_bucket", fetched_bucket)
        object.__setattr__(self, "_bands", dict(bands) if bands else {})

    @property
    def bands(self) -> dict[str, str]:
        """A shallow copy -- mutating it cannot corrupt this Evidence."""
        return dict(self._bands)

    def __len__(self) -> int:
        return len(self.text)

    def to_calldata(self, *, include_text: bool = False) -> dict[str, Any]:
        """Deterministically-keyed dict of calldata-safe primitives."""
        payload: dict[str, Any] = {
            "bands": dict(self._bands),
            "fetched_bucket": self.fetched_bucket,
            "fingerprint": self.fingerprint,
            "policy_id": self.policy_id,
            "status": self.status,
            "url": self.url,
        }
        if include_text:
            payload["text"] = self.text
        return {key: payload[key] for key in sorted(payload)}

    def summary(self) -> str:
        return "Evidence({0} status={1} {2} chars={3} bands={4})".format(
            self.url,
            self.status,
            self.fingerprint,
            len(self.text),
            len(self._bands),
        )



# ============================================================================
# --- from webanchor/pipeline.py ---
# ============================================================================

"""The normalization path, and the two public entry points built on it.

:func:`normalize` is the pure normalization core: raw HTML in, anchorable text out.
:func:`anchor_html` wraps it with detection, fingerprinting and an
:class:`~webanchor.Evidence`; :func:`anchor` is :func:`anchor_html` with a
GenLayer fetch in front of it.  There is one pipeline implementation, not two
-- see :func:`anchor_html` for why that is a requirement and not a tidiness
preference.

:func:`normalize` is the whole normalization pipeline composed in one place.  It touches
no network, no clock and no GenLayer runtime -- it is a pure function of
``(html, policy)``, which is what makes the entire divergence story testable
without a validator set.

Stage order, and why it is not arbitrary
----------------------------------------
``strip_html`` -> ``canonicalize_text`` -> ``quantize_timestamps`` ->
``band_numbers``

1. **strip_html first.** Everything downstream operates on prose.  Running a
   number matcher over raw markup would band the digits inside
   ``<h1 class="col-6">`` and inside every URL and hex color in an attribute.
2. **canonicalize_text second.** The timestamp and number matchers are written
   against ASCII separators and ASCII digits-with-ASCII-punctuation.  A page
   that emits a narrow no-break space inside ``1 234,56`` or an en dash inside
   a date range must be folded to ASCII *before* those matchers run, or the
   matchers see two different strings for one rendered value -- which is the
   exact divergence they exist to remove.
3. **quantize_timestamps third, and this is the load-bearing ordering
   decision.** ``2024-04-12`` is three numbers to the number matcher.  If
   banding ran first it would rewrite the date's digits (rounding ``2024`` to
   ``2020`` under three significant digits) and the timestamp matcher would
   then find nothing to redact -- a mangled date *and* a leaked timestamp,
   from one wrong ordering.  Timestamps are consumed first, replaced by
   digit-free tokens (``[timestamp]``, ``[relative-time]``), and only then is
   the remaining text handed to the number matcher.
   ``test_timestamps_are_consumed_before_numbers`` pins this.
4. **band_numbers last**, over text that no longer contains dates.

Idempotency
-----------
:func:`normalize` output is idempotent under ``number_band_mode`` ``none``
(the default policy) and ``significant`` (the strict policy), in the sense
that canonicalizing the returned text again is a no-op.  It is **not**
idempotent under ``grid``, because a band token ``[1200~1300]`` contains
two fresh numbers that a second pass would band again.  That boundary is
tested in ``tests/test_pipeline.py`` rather than asserted away.
"""

from typing import Mapping, Optional


__all__ = ["normalize", "anchor", "anchor_html"]


def normalize(html: str, policy: Policy) -> tuple[str, dict[str, str]]:
    """Run the full pure normalization path over ``html``.

    Returns the normalized text and the ``bands`` mapping from
    :func:`webanchor.numbers.band_numbers` -- original matched substring to
    replacement -- which is what an :class:`~webanchor.Evidence` carries as
    its audit trail.  With ``number_band_mode="none"`` the mapping is empty.

    Raises:
        ContentTooLarge, NotTextual, EmptyContent: propagated from
            :func:`~webanchor.html_strip.strip_html`.  They are not caught
            here: a page that cannot be stripped has no evidence in it, and
            blueprint rule R4 forbids returning a partial result in that case.
    """
    text = strip_html(html, policy)
    text = canonicalize_text(text, policy)
    text = quantize_timestamps(text, policy)
    return band_numbers(text, policy)


def anchor_html(
    html: str,
    url: str,
    policy: Optional[Policy] = None,
    *,
    status: int = 200,
    headers: Optional[Mapping[str, str]] = None,
    fetched_bucket: int = 0,
) -> Evidence:
    """Anchor HTML you already have: detection, normalization, fingerprint.

    This is :func:`anchor` with the network removed and **nothing else
    changed**.  It is not a test helper that approximates the real path -- it
    *is* the real path; :func:`anchor` is a thin fetch in front of a call to
    this function.  Keeping it that way is deliberate: a demonstration path
    that merely resembled production would prove nothing about production, and
    the entire divergence corpus, every end-to-end test, and every
    off-chain demo of this library run through here.  If the two ever diverge,
    the tests stop testing the shipped code.

    Args:
        html: The response body as text.  Passed to
            :func:`webanchor.detect.check_response` **before** stripping,
            because challenge markers live in markup that stripping deletes.
        url: Recorded on the :class:`~webanchor.Evidence` and attached to any
            error raised, for diagnosis.  Not fetched.
        policy: Defaults to :meth:`webanchor.Policy.default`.  The default is
            constructed here rather than as a parameter default so that a
            future change to ``Policy.default()`` cannot be frozen into this
            function's signature at import time.
        status: The status that accompanied ``html``, if you have one.  The
            ``200`` default is the honest value for content that arrived
            through a renderer, which does not expose a status at all -- see
            :mod:`webanchor.fetch`.
        headers: Response headers, if you have them.  Optional because render
            mode never does; their absence only weakens the
            ``Server:``-assisted branch of bot-wall detection.
        fetched_bucket: A caller-supplied quantized fetch time, defaulting to
            ``0``.  **This library never reads a clock**, and that is a design
            requirement rather than an omission: a value derived from
            ``time.time()`` inside the library would differ on every validator
            and would be folded straight into the consensus artifact,
            manufacturing the exact divergence WebAnchor exists to remove.  A
            library that reads wall-clock time to build a consensus artifact
            is self-defeating.  If you want a fetch bucket in the evidence,
            quantize a clock reading *outside* the library -- ideally one the
            validators already agree on, such as a block timestamp -- and pass
            it in.  Then, and only then, is it a shared fact rather than a
            per-node reading.  Note that a bucket still straddles: see
            :mod:`webanchor.behavior`.

    Returns:
        An :class:`~webanchor.Evidence` whose ``fingerprint`` is what
        ``gl.eq_principle.strict_eq`` compares.

    Raises:
        WebAnchorError: any subclass, from detection
            (:func:`~webanchor.detect.check_response`) or from normalization
            (:func:`normalize`).  Nothing is caught and nothing is softened --
            R4 forbids returning a partial Evidence, because a partial
            Evidence is indistinguishable from a real one once it is in
            calldata.
    """
    # check_response is defined earlier in this same flattened module.

    if policy is None:
        policy = Policy.default()

    check_response(status, headers or {}, html, policy, url=url)

    text, bands = normalize(html, policy)
    policy_id = policy.policy_id
    return Evidence(
        url=url,
        status=status,
        fingerprint=fingerprint(text, policy_id),
        policy_id=policy_id,
        text=text,
        bands=bands,
        fetched_bucket=fetched_bucket,
    )


def anchor(
    url: str,
    policy: Optional[Policy] = None,
    *,
    mode: str = "html",
    wait_after_loaded: Optional[str] = None,
    fetched_bucket: int = 0,
) -> Evidence:
    """Fetch ``url`` and anchor it: the full path, network included.

    ``fetch_raw`` -> ``check_response`` -> ``normalize`` -> ``fingerprint`` ->
    :class:`~webanchor.Evidence`.  Every step after the fetch is delegated to
    :func:`anchor_html`, which is the same function the offline tests and the
    benchmark corpus exercise, so there is exactly one implementation of the pipeline.

    ``webanchor.fetch`` is imported **inside this function body**, never at
    module scope.  ``pipeline`` is imported by ``webanchor/__init__.py``, so a
    module-scope import here would drag the GenLayer dependency into
    ``import webanchor`` and break the library on every machine without a
    GenVM -- blueprint rule R2.  The cost is deferred failure; the benefit is
    that ``import webanchor`` and ``anchor_html`` work everywhere, and only
    ``anchor`` itself requires the runtime.

    Args:
        url: The absolute URL to read.
        policy: Defaults to :meth:`webanchor.Policy.default`.
        mode: ``"html"``/``"text"`` (renderer) or ``"get"`` (raw HTTP).  Read
            :mod:`webanchor.fetch` on the render-mode blind spot: render mode
            reports no status, so a 429 is detectable only from the body.
        wait_after_loaded: Renderer settle time, e.g. ``"5s"``.
        fetched_bucket: See :func:`anchor_html`.  Still not a clock read.

    Returns:
        An :class:`~webanchor.Evidence` ready for
        ``gl.eq_principle.strict_eq``.

    Raises:
        WebAnchorError: if the GenLayer SDK is absent -- this function, alone
            in the library, requires a GenVM contract execution.  Use
            :func:`anchor_html` off-chain.
        NetworkError: the SDK fetch failed.
        Everything :func:`anchor_html` raises.
    """
    # fetch_raw is defined earlier in this same flattened module.

    status, headers, body = fetch_raw(
        url, mode=mode, wait_after_loaded=wait_after_loaded
    )
    return anchor_html(
        body,
        url,
        policy,
        status=status,
        headers=headers,
        fetched_bucket=fetched_bucket,
    )



# ============================================================================
# --- from webanchor/fetch.py ---
# ============================================================================

"""The only module in WebAnchor permitted to touch GenLayer.

Blueprint rule R2 confines every ``genlayer`` / ``gl`` reference to this file,
and confines it further to the *inside of function bodies*.  Both halves
matter:

* **Only this file**, so the rest of the library is a pure function of
  strings and is testable with plain ``pytest`` on any machine -- no GenVM,
  no node, no network.  ``tests/test_constraints.py`` parses the package with
  ``ast`` and enforces this rather than trusting review.
* **Only inside function bodies**, so that ``import webanchor`` succeeds on a
  developer laptop with no SDK installed.  A module-scope ``import genlayer``
  here would propagate an ``ImportError`` through
  ``webanchor.pipeline.anchor`` to ``webanchor/__init__.py`` and make the
  whole library unimportable off-chain, which would take the entire test
  suite with it.  The failure is deferred to the moment somebody actually
  *calls* :func:`fetch_raw`, where it is both unavoidable and actionable.

The SDK surface this targets
----------------------------
GenLayer SDK v0.1.3+ / executors v0.3:

* ``gl.nondet.web.get(url)`` -> an object with ``.status``, ``.headers`` and
  ``.body`` (bytes).
* ``gl.nondet.web.render(url, mode="text"|"html", wait_after_loaded="5s")``
  -> ``str``.

``gl.get_webpage`` is the **pre-v0.1.3 name** and no longer exists.  See
:func:`get_webpage` below for the compatibility shim and why it is a shim
rather than a re-export.

The render-mode blind spot, stated plainly
------------------------------------------
``render`` returns a **string and nothing else**.  There is no status code and
there are no headers, because the headless browser has already followed
redirects, executed scripts and settled on a DOM.  WebAnchor therefore reports
``status=200`` and ``headers={}`` for every render-mode fetch.

That is not a placeholder to be fixed later; it is a limitation of the
transport, and it changes the threat model:

* A 429 arriving in render mode is **invisible to status checking**.  The
  rate-limit page renders, comes back as a perfectly ordinary string, and
  ``from_status(200)`` says everything is fine.
* Likewise a 404 that the origin renders as a styled error page, and a 403
  block page.

This is exactly why :mod:`webanchor.detect` does body-based bot-wall and
soft-error detection instead of stopping at the status line.  In ``get`` mode
those body checks are a second line of defense; in ``render`` mode they are
the *only* line of defense, and the ``Server:``-header branch of bot-wall
detection is unavailable too, because there are no headers.

Practical consequence: prefer ``mode="get"`` when the page does not need
JavaScript, because it gives detection strictly more to work with.  Use
``render`` when the content genuinely requires a browser, and understand that
you are trading status visibility for it.
"""

from typing import Optional


__all__ = ["fetch_raw", "get_webpage", "FETCH_MODES", "RENDER_MODES"]

#: Fetch modes accepted by :func:`fetch_raw`, in a fixed tuple (R3).
FETCH_MODES: tuple[str, ...] = ("html", "text", "get")

#: The subset of :data:`FETCH_MODES` that routes through ``web.render`` and
#: therefore carries the no-status/no-headers limitation documented above.
RENDER_MODES: tuple[str, ...] = ("html", "text")

_IMPORT_HELP = (
    "webanchor.fetch requires the GenLayer SDK, which is only present inside "
    "a GenVM contract execution. This function cannot work on a developer "
    "machine or in CI. Everything else in WebAnchor is pure and runs without "
    "it: to exercise the full pipeline off-chain, pass HTML directly to "
    "webanchor.anchor_html(html, url) instead of calling webanchor.anchor(url)."
)


def _import_gl():
    """Import the ``gl`` binding lazily, or raise a WebAnchorError that helps.

    A bare ``ImportError`` reaching a contract author says "No module named
    'genlayer'", which is true and useless.  It does not say that the rest of
    the library works fine without it, and it does not point at
    :func:`webanchor.anchor_html`, which is what the caller almost always
    wants when they hit this off-chain.  Rule R4 asks for loud failures;
    a loud failure that also says what to do instead is strictly better.
    """
    try:
        from genlayer import gl  # noqa: F401  (imported for the caller)
    except ImportError as exc:
        raise WebAnchorError("{0} (import failed: {1})".format(_IMPORT_HELP, exc))
    return gl


def fetch_raw(
    url: str,
    *,
    mode: str = "html",
    wait_after_loaded: Optional[str] = None,
) -> tuple[int, dict[str, str], str]:
    """Fetch ``url`` through the GenLayer non-deterministic web block.

    Args:
        url: The absolute URL to read.
        mode: ``"html"`` or ``"text"`` route through ``gl.nondet.web.render``
            and return rendered DOM text; ``"get"`` routes through
            ``gl.nondet.web.get`` and returns the raw body decoded as UTF-8
            with ``errors="replace"``.  Replacement rather than strict
            decoding is deliberate: a mis-encoded byte must not raise here and
            deprive :mod:`webanchor.detect` of the chance to say *why* the
            response is unusable, and the resulting U+FFFD characters are
            deterministic, so no divergence is introduced.
        wait_after_loaded: Passed straight to ``render`` (e.g. ``"5s"``).
            Ignored in ``get`` mode, which has no page lifecycle to wait on.
            Note that a longer wait widens the wall-clock spread between
            validators, which increases residual divergence for any quantized
            value on the page -- see :mod:`webanchor.behavior`.

    Returns:
        ``(status, headers, body_text)``.  In the two render modes the SDK
        exposes neither a status nor headers, so this is **always**
        ``(200, {}, text)``.  Read the module docstring before relying on that
        200: it means "render mode", not "the origin said OK".

    Raises:
        WebAnchorError: ``genlayer`` cannot be imported -- you are not inside
            a GenVM contract.  The message names
            :func:`webanchor.anchor_html` as the off-chain alternative.
        WebAnchorError: ``mode`` is not one of :data:`FETCH_MODES`.  Raised
            *before* the SDK import so the message is about the typo rather
            than about a missing dependency.
        NetworkError: the SDK call itself failed, for any reason.  The
            original exception's type and message are preserved in ``detail``
            rather than discarded -- a typed error that erases the cause
            replaces one debugging problem with another.
    """
    if mode not in FETCH_MODES:
        raise WebAnchorError(
            "unknown fetch mode {0!r}; expected one of {1}".format(
                mode, list(FETCH_MODES)
            ),
            url=url,
        )

    gl = _import_gl()

    if mode == "get":
        try:
            response = gl.nondet.web.get(url)
        except Exception as exc:
            raise NetworkError(
                "gl.nondet.web.get failed: {0}: {1}".format(
                    type(exc).__name__, exc
                ),
                url=url,
            ) from exc
        status = int(response.status)
        headers = {str(k): str(v) for k, v in dict(response.headers).items()}
        body = response.body
        if isinstance(body, (bytes, bytearray)):
            text = bytes(body).decode("utf-8", errors="replace")
        else:
            text = str(body)
        return status, headers, text

    try:
        rendered = gl.nondet.web.render(
            url, mode=mode, wait_after_loaded=wait_after_loaded
        )
    except Exception as exc:
        raise NetworkError(
            "gl.nondet.web.render failed: {0}: {1}".format(
                type(exc).__name__, exc
            ),
            url=url,
        ) from exc
    # No status, no headers -- see "The render-mode blind spot" above.
    return 200, {}, str(rendered)


def get_webpage(url: str, mode: str = "text") -> str:
    """Compatibility shim for the removed ``gl.get_webpage``.

    ``gl.get_webpage(url, mode)`` was the GenLayer SDK's page-fetch entry point
    **before v0.1.3**.  It no longer exists: the current SDK spells it
    ``gl.nondet.web.render(url, mode=...)``.  Contracts and tutorials written
    against the old name are still in circulation, so this function exists so
    that the familiar call site keeps working -- it is a *rename adapter*, not
    a re-export, and there is no ``gl.get_webpage`` behind it to fall back to.

    It delegates to :func:`fetch_raw`, which delegates to
    ``gl.nondet.web.render``, and returns only the body string, discarding the
    synthetic ``200`` and the empty header dict that render mode produces.

    Prefer :func:`webanchor.anchor` over this. This returns a raw string with
    no detection and no normalization applied -- the two things WebAnchor is
    for. It is here for familiarity during a port, not as a recommended API.

    Raises:
        WebAnchorError: ``genlayer`` is not importable, or ``mode`` is not a
            render mode.
        NetworkError: the underlying SDK call failed.
    """
    if mode not in RENDER_MODES:
        raise WebAnchorError(
            "get_webpage mode must be one of {0}, got {1!r}; it maps onto "
            "gl.nondet.web.render, which has no other modes".format(
                list(RENDER_MODES), mode
            ),
            url=url,
        )
    _status, _headers, text = fetch_raw(url, mode=mode)
    return text


# ============================================================================
# --- from webanchor/__init__.py (re-exports only; all names already defined
#     above in this flattened module) ---
# ============================================================================
__version__ = "0.1.0"
