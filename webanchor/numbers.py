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

from .policy import Policy
from .timestamps import CANONICAL_TIMESTAMP_RE, TIMESTAMP_TOKEN

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
