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

from .behavior import BEHAVIOR_VERSION
from .errors import PolicyError

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
#: ``percent`` was REMOVED in the M4 corrections and is deliberately not kept
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
    use.  It was a ``float`` through M3, and that was the last float left in a
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

    Defaults to ``False``.  Measured on the M5 benchmark corpus
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
