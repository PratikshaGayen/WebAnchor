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

from .policy import Policy

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
