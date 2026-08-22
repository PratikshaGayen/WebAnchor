"""Number detection and banding: exact decimal arithmetic, one fixed rule."""

import ast
import os
from decimal import Decimal

import pytest

from webanchor import Policy
from webanchor.errors import PolicyError
from webanchor.numbers import band_numbers, render_decimal

NONE = Policy.default()
SIG = Policy.default().with_changes(number_band_mode="significant")
SIG1 = SIG.with_changes(number_significant_digits=1)
SIG5 = SIG.with_changes(number_significant_digits=5)
#: Enough significant digits that rounding is a no-op, so parse-rule tests
#: assert the parse and nothing else.
SIG10 = SIG.with_changes(number_significant_digits=10)
GRID100 = Policy.default().with_changes(
    number_band_mode="grid", number_grid_step="100"
)
GRID1000 = GRID100.with_changes(number_grid_step="1000")
GRID_HALF = GRID100.with_changes(number_grid_step="0.5")


def band(text, policy):
    return band_numbers(text, policy)[0]


# ---------------------------------------------------------------------------
# No float, anywhere -- enforced structurally, not by review
# ---------------------------------------------------------------------------


def test_numbers_module_never_names_float():
    """A single ``float()`` in this path would reintroduce build-dependent digits."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "webanchor",
        "numbers.py",
    )
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "float", "float() call at line {0}".format(
                node.lineno
            )
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            pytest.fail(
                "float literal {0!r} at line {1}".format(node.value, node.lineno)
            )


def test_grid_step_is_a_string_field_so_no_float_can_reach_the_lattice():
    """``Decimal(0.1)`` is 0.1000000000000000055...; ``Decimal("0.1")`` is not.

    The step is a Policy field, so the type is the guard: a float never
    reaches the arithmetic because a float is refused at construction.
    """
    with pytest.raises(PolicyError):
        Policy.default().with_changes(
            number_band_mode="grid", number_grid_step=0.1
        )
    policy = Policy.default().with_changes(
        number_band_mode="grid", number_grid_step="0.1"
    )
    assert policy.grid_step() == Decimal("0.1")
    assert band("1000.05", policy) == "[1000.0~1000.1]"


# ---------------------------------------------------------------------------
# Mode: none
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["1,234.56", "$99", "no digits here", "", "12.5%", "1.2.3"]
)
def test_none_mode_changes_nothing(text):
    result, bands = band_numbers(text, NONE)
    assert result == text
    assert bands == {}


# ---------------------------------------------------------------------------
# Detection: separators, currencies, signs, percents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1,234.56", "1230"),  # en-US
        ("1.234,56", "1230"),  # de-DE
        ("1 234,56", "1230"),  # fr-FR, plain space
        ("1 234,56", "1230"),  # NBSP
        ("1 234,56", "1230"),  # narrow NBSP
        ("1,234,567.89", "1230000"),
        ("1.234.567,89", "1230000"),
        ("1 234 567,89", "1230000"),
        ("1234", "1230"),
        ("1.5", "1.5"),
        ("0,75", "0.75"),
        ("0", "0"),
        ("12345.678", "12300"),
    ],
)
def test_separator_conventions_all_reach_the_same_value(text, expected):
    assert band(text, SIG) == expected


@pytest.mark.parametrize("symbol", ["$", "€", "£", "¥"])
def test_leading_currency_symbol_is_preserved(symbol):
    assert band(symbol + "1,234.56", SIG) == symbol + "1230"


def test_space_between_currency_and_digits_is_normalized_away():
    """"$ 5" and "$5" are the same price and must not fingerprint differently."""
    assert band("$ 1234", SIG) == band("$1234", SIG) == "$1230"


def test_trailing_percent_is_preserved():
    assert band("12.5%", SIG) == "12.5%"
    assert band("12.5 %", SIG) == "12.5%"


def test_negative_values_keep_their_sign():
    assert band("-42.7", SIG) == "-42.7"
    assert band("-1234", SIG) == "-1230"


def test_explicit_plus_is_normalized_away():
    """A page that alternates "+3.5" and "3.5" must converge, so + is dropped."""
    assert band("+3.5", SIG) == band("3.5", SIG) == "3.5"


def test_numbers_inside_prose_are_found_and_the_prose_survives():
    assert band("Order 100418 shipped, 3 items, total $1,234.56.", SIG) == (
        "Order 100000 shipped, 3 items, total $1230."
    )


# ---------------------------------------------------------------------------
# The documented ambiguity rule
# ---------------------------------------------------------------------------


def test_ambiguous_single_separator_with_three_digits_is_thousands():
    """The documented tie-break: ``1,234`` and ``1.234`` are BOTH 1234.

    ``1.234`` meaning one-point-two-three-four is read as 1234.  That is the
    accepted cost of a fixed rule: every validator is wrong identically, which
    costs an approximate band instead of consensus.
    """
    assert band("1,234", SIG10) == "1234"
    assert band("1.234", SIG10) == "1234"


def test_single_separator_with_non_three_digits_is_decimal():
    assert band("1.5", SIG10) == "1.5"
    assert band("1,25", SIG10) == "1.25"
    assert band("0.1234", SIG10) == "0.1234"


def test_more_than_three_leading_digits_forces_a_decimal_reading():
    """``12345.678`` cannot be thousands grouping: groups must start 1-3 digits."""
    assert band("12345.678", SIG10) == "12345.678"


def test_repeated_single_separator_is_always_thousands():
    assert band("1.234.567", SIG10) == "1234567"
    assert band("1,234,567", SIG10) == "1234567"


def test_mixed_separators_use_the_last_one_as_decimal():
    """Convention-independent and never wrong."""
    assert band("1,234.56", SIG10) == "1234.56"
    assert band("1.234,56", SIG10) == "1234.56"


def test_space_class_separators_are_never_decimal():
    assert band("1 234", SIG10) == "1234"
    assert band("1 234", SIG10) == "1234"


@pytest.mark.parametrize(
    "text", ["1.2.3", "192.168.0.1", "10.0.0.255", "1.22.333", "12345.6789.0"]
)
def test_invalid_grouping_is_left_completely_untouched(text):
    """Version strings and IPs must not be silently rewritten into numbers."""
    result, bands = band_numbers(text, SIG)
    assert result == text
    assert bands == {}


def test_a_space_before_fewer_than_three_digits_is_not_a_separator():
    """"Widget 42 3" is two numbers, not 423."""
    assert band("Widget 42 3", SIG10) == "Widget 42 3"
    assert band("Widget 42 3", GRID_HALF) == "Widget [42.0~42.5] [3.0~3.5]"


def test_the_ambiguity_rule_is_not_per_occurrence():
    """The same literal resolves identically regardless of its neighbours.

    A locale-sniffing heuristic would read the ``1,234`` differently in these
    two strings; that input-dependence is exactly what turns an ambiguity into
    a divergence.
    """
    a = band_numbers("1,234 and 9.876,54", SIG10)[1]["1,234"]
    b = band_numbers("1,234 and 9,876.54", SIG10)[1]["1,234"]
    assert a == b == "1234"


# ---------------------------------------------------------------------------
# Mode: significant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,digits,expected",
    [
        ("1234", 3, "1230"),
        ("1234", 1, "1000"),
        ("1234", 5, "1234"),
        ("0.00012345", 3, "0.000123"),
        ("999999", 3, "1000000"),
        ("0", 3, "0"),
        ("-1234", 3, "-1230"),
    ],
)
def test_significant_rounding(text, digits, expected):
    policy = SIG.with_changes(number_significant_digits=digits)
    assert band(text, policy) == expected


def test_significant_rounding_is_half_even():
    """Explicit ROUND_HALF_EVEN, not whatever the ambient context holds."""
    policy = SIG.with_changes(number_significant_digits=2)
    assert band("1.25", policy) == "1.2"  # ties to even
    assert band("1.35", policy) == "1.4"  # ties to even


def test_significant_output_is_never_scientific_notation():
    for text in ["1234567890", "0.000000123", "999999999999"]:
        assert "E" not in band(text, SIG)
        assert "e" not in band(text, SIG)


def test_significant_mode_is_idempotent():
    text = "Prices: $1,234.56, 78.9%, 1 000 000, 0.00012345, -42.7"
    once = band(text, SIG)
    assert band(once, SIG) == once
    assert band(band(once, SIG), SIG) == once


def test_significant_mode_collapses_last_digit_jitter():
    """The point of the mode: two captures of a ticking counter converge."""
    assert band("12841 views", SIG) == band("12849 views", SIG) == "12800 views"


def test_render_decimal_strips_trailing_zeros_for_idempotency():
    assert render_decimal(Decimal("1.230")) == "1.23"
    assert render_decimal(Decimal("1.000")) == "1"
    assert render_decimal(Decimal("-0")) == "0"
    assert render_decimal(Decimal("1.23E+3")) == "1230"


# ---------------------------------------------------------------------------
# Mode: grid -- the replacement for the removed percent mode
# ---------------------------------------------------------------------------


def test_grid_band_is_a_readable_closed_interval():
    """Readable on purpose -- this token goes into an LLM prompt."""
    assert band("1250", GRID100) == "[1200~1300]"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1234", "[1200~1300]"),
        ("1200", "[1200~1300]"),
        ("1299", "[1200~1300]"),
        ("1300", "[1300~1400]"),
        ("0", "[0~100]"),
        ("99", "[0~100]"),
        ("$1,234.56", "$[1200~1300]"),
    ],
)
def test_grid_band_rendering(text, expected):
    assert band(text, GRID100) == expected


def test_grid_band_endpoints_are_rendered_at_the_scale_of_the_step():
    """Not at the scale of the value -- that is what makes the token shared.

    ``1.20`` and ``1.2`` are the same number written two ways.  If the band's
    precision came from the reading, they would render ``[1.00~1.50]`` and
    ``[1.0~1.5]`` -- two strings for one bucket, and ``strict_eq`` would fail
    on a difference that exists only in the source markup.
    """
    assert band("1.20", GRID_HALF) == band("1.2", GRID_HALF) == "[1.0~1.5]"
    assert band("1250", GRID100) == "[1200~1300]"


def test_grid_band_always_contains_its_own_value():
    """low <= v < high, by construction: floor down, then add one step."""
    for text in ["1250", "1", "0", "99999", "7", "100"]:
        low, high = band(text, GRID100).strip("[]").split("~")
        assert Decimal(low) <= Decimal(text) < Decimal(high)


def test_a_wider_step_gives_a_wider_bucket():
    narrow = band("1234", GRID100).strip("[]").split("~")
    wide = band("1234", GRID1000).strip("[]").split("~")
    assert Decimal(wide[1]) - Decimal(wide[0]) > Decimal(narrow[1]) - Decimal(
        narrow[0]
    )


def test_grid_mode_DOES_make_nearby_values_converge():
    """The whole reason ``percent`` was removed, asserted head-on.

    ``percent`` centred a band on each validator's own reading, so 1000 gave
    ``[900~1100]`` and 1050 gave ``[945~1155]``: overlapping intervals,
    different strings, and ``strict_eq`` compares strings.  ``grid`` floors
    both onto one shared lattice, so two readings inside a single step produce
    the IDENTICAL token.  That is convergence; the other was decoration.
    """
    assert band("1000", GRID100) == band("1050", GRID100) == "[1000~1100]"
    assert band("1000", GRID100) == band("1099.99", GRID100)


def test_grid_convergence_holds_at_a_fractional_step():
    assert band("1.10", GRID_HALF) == band("1.49", GRID_HALF) == "[1.0~1.5]"
    assert band("1.5", GRID_HALF) == "[1.5~2.0]"


def test_grid_does_not_converge_across_a_bucket_edge():
    """The honest boundary, per the general statement in behavior.py.

    Grid is a bucketing scheme, so it inherits the straddle property: two
    readings either side of a lattice edge diverge as completely as with no
    banding at all.  Risk falls as ``spread / step``; it never reaches zero.
    """
    assert band("1099", GRID100) != band("1100", GRID100)
    assert band("1099", GRID100) == "[1000~1100]"
    assert band("1100", GRID100) == "[1100~1200]"


def test_grid_floors_negatives_downward_not_toward_zero():
    """``Decimal`` truncation would put -50 in ``[0~100]``, which is false."""
    assert band("-50", GRID100) == "[-100~0]"
    assert band("-100", GRID100) == "[-100~0]"
    assert band("-101", GRID100) == "[-200~-100]"


def test_grid_step_of_one_buckets_between_consecutive_integers():
    policy = Policy.default().with_changes(number_band_mode="grid")
    assert policy.number_grid_step == "1"
    assert band("7", policy) == "[7~8]"
    assert band("7.9", policy) == "[7~8]"


def test_grid_step_tiles_the_line_exactly_at_a_tenth():
    """Ten tenth-wide buckets must tile [0, 1) with no gap and no overlap."""
    policy = Policy.default().with_changes(
        number_band_mode="grid", number_grid_step="0.1"
    )
    assert band("0.3", policy) == "[0.3~0.4]"
    assert band("0.7", policy) == "[0.7~0.8]"
    assert band("0.29999", policy) == "[0.2~0.3]"


def test_grid_step_larger_than_one_with_a_positive_exponent_string():
    policy = Policy.default().with_changes(
        number_band_mode="grid", number_grid_step="1E+2"
    )
    assert band("1250", policy) == "[1200~1300]"


def test_percent_mode_no_longer_exists_at_all():
    """Removed, not deprecated: a footgun with a friendly name is worse."""
    from webanchor.policy import _NUMBER_BAND_MODES

    assert "percent" not in _NUMBER_BAND_MODES
    assert _NUMBER_BAND_MODES == ("none", "significant", "grid")
    assert not hasattr(Policy.default(), "number_band_percent")
    with pytest.raises(PolicyError):
        Policy(number_band_mode="percent")


def test_significant_mode_also_converges():
    """The other convergent mode; grid is not the only one."""
    assert band("1231", SIG) == band("1234", SIG) == "1230"


def test_grid_mode_is_not_idempotent_and_this_is_documented():
    """Honest boundary: a band token contains fresh numbers.

    ``[1200~1300]`` re-banded yields ``[[...]~[...]]``.  Band a page once.
    This is asserted rather than quietly hoped for.
    """
    once = band("1250", GRID100)
    twice = band(once, GRID100)
    assert once == "[1200~1300]"
    assert twice != once
    assert twice.count("[") > once.count("[")


# ---------------------------------------------------------------------------
# The bands audit trail
# ---------------------------------------------------------------------------


def test_bands_maps_original_substring_to_replacement():
    _, bands = band_numbers("Total $1,234.56 with 12.5% VAT", SIG)
    assert bands == {"$1,234.56": "$1230", "12.5%": "12.5%"}


def test_bands_keys_are_the_original_text_verbatim():
    text = "1 234,56"
    _, bands = band_numbers(text, SIG)
    assert list(bands) == [text]


def test_repeated_occurrences_share_one_band_entry():
    result, bands = band_numbers("1234 then 1234 again", SIG)
    assert result == "1230 then 1230 again"
    assert bands == {"1234": "1230"}


def test_bands_is_empty_when_nothing_matched():
    result, bands = band_numbers("no numerals at all", SIG)
    assert result == "no numerals at all"
    assert bands == {}


def test_bands_insertion_order_follows_first_occurrence():
    """dict is insertion-ordered; first-occurrence order is input-determined."""
    _, bands = band_numbers("9999 then 1111 then 9999", SIG)
    assert list(bands) == ["9999", "1111"]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "policy,pid", [(SIG, "sig"), (GRID100, "grid")], ids=["sig", "grid"]
)
def test_output_is_identical_over_repeated_runs(policy, pid):
    text = "Prices: $1,234.56, 1.234,56, 1 234,56, 78.9%, -42.7, 1.2.3"
    first = band_numbers(text, policy)
    for _ in range(50):
        assert band_numbers(text, policy) == first
