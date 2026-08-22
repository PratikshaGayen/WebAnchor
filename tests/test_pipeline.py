"""The composed normalization path, and the ordering that makes it correct."""

import io
import os

import pytest

from webanchor import Policy
from webanchor.errors import ContentTooLarge, EmptyContent, NotTextual
from webanchor.html_strip import strip_html
from webanchor.numbers import band_numbers
from webanchor.pipeline import normalize
from webanchor.text import canonicalize_text
from webanchor.timestamps import quantize_timestamps

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

DEFAULT = Policy.default()
STRICT = Policy.strict()
QUANTIZE = Policy.strict().with_changes(timestamp_mode="quantize")
GRID = Policy.default().with_changes(
    number_band_mode="grid", number_grid_step="100"
)


def fixture(name):
    with io.open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as handle:
        return handle.read()


FIXTURE_NAMES = sorted(
    n for n in os.listdir(FIXTURE_DIR) if n.endswith(".html")
)


# ---------------------------------------------------------------------------
# THE ORDERING. Timestamps must be consumed before numbers.
# ---------------------------------------------------------------------------

BOTH = "<p>Order shipped on 2024-04-12. Price: $1,234.56.</p>"


def test_timestamps_are_consumed_before_numbers():
    """A date and a price in one string; only one stage order is correct.

    ``2024-04-12`` is three numbers to the number matcher.  Run banding first
    and the date is mangled into rounded digits *and* the timestamp matcher
    then finds nothing left to redact -- two failures from one wrong order.
    """
    text, bands = normalize(BOTH, STRICT)

    assert text == "order shipped on [timestamp]. price: $1230."
    assert bands == {"$1,234.56": "$1230"}

    # The date never reached the number matcher.
    assert "2024" not in text
    assert "2020" not in text
    assert "[timestamp]" in text


def test_the_reverse_order_demonstrably_breaks_the_date():
    """Proof that the ordering choice is load-bearing, not cosmetic.

    This reconstructs the wrong pipeline by hand and shows it produces both a
    mangled date and a leaked timestamp -- so the assertion above is pinning a
    real property rather than restating whatever the code happens to do.
    """
    stripped = canonicalize_text(strip_html(BOTH, STRICT), STRICT)

    wrong_text, _ = band_numbers(stripped, STRICT)
    wrong_text = quantize_timestamps(wrong_text, STRICT)

    assert "[timestamp]" not in wrong_text, "the date was destroyed before redaction"
    assert "2020" in wrong_text, "the date's digits were banded as plain numbers"
    assert wrong_text != normalize(BOTH, STRICT)[0]


def test_ordering_also_matters_in_quantize_mode():
    text, _ = normalize(BOTH, QUANTIZE)
    assert "2024-04-12t00:00:00z" in text.lower()
    assert "$1230" in text


def test_a_quantized_timestamp_is_not_mangled_by_number_banding():
    """Regression: stage order alone is not enough in quantize mode.

    Ordering protects the timestamp matcher's *input*.  It does nothing for
    its *output*: ``2024-04-12T00:00:00Z`` is nine digits, and an unguarded
    number matcher rounds it into ``2020-4-12T0:0:0Z`` -- silently undoing the
    canonicalization the previous stage just performed, and producing a string
    that is not a timestamp in any format.  ``band_numbers`` therefore treats
    canonical timestamps as protected spans.
    """
    text, bands = normalize(BOTH, QUANTIZE)
    assert "2024-04-12T00:00:00Z" in text
    assert "2020-4-12" not in text
    assert bands == {"$1,234.56": "$1230"}


def test_protection_is_scoped_to_canonical_timestamps_only():
    """Guard on the guard: ordinary numbers next to one are still banded."""
    text, _ = band_numbers("at 2024-04-12T00:00:00Z we sold 1234", STRICT)
    assert text == "at 2024-04-12T00:00:00Z we sold 1230"


def test_the_redaction_token_survives_banding_too():
    text, _ = band_numbers("on [timestamp] we sold 1234", STRICT)
    assert text == "on [timestamp] we sold 1230"


def test_quantize_mode_pipeline_is_stable_on_a_second_pass():
    """The protection is what makes re-running quantize+band a fixed point."""
    once, _ = normalize(BOTH, QUANTIZE)
    again = quantize_timestamps(canonicalize_text(once, QUANTIZE), QUANTIZE)
    again, _ = band_numbers(again, QUANTIZE)
    assert again == once


def test_stage_order_is_the_documented_one():
    """Composition equality: normalize IS the four stages in that order."""
    for policy in (DEFAULT, STRICT, QUANTIZE, GRID):
        step = strip_html(BOTH, policy)
        step = canonicalize_text(step, policy)
        step = quantize_timestamps(step, policy)
        expected = band_numbers(step, policy)
        assert normalize(BOTH, policy) == expected


def test_html_is_stripped_before_canonicalization():
    """Markup must never reach the number or timestamp matchers."""
    html = '<div class="col-12" data-id="2024-04-12"><p>Total 5</p></div>'
    text, _ = normalize(html, STRICT)
    assert text == "total 5"
    assert "col" not in text and "2024" not in text


def test_canonicalization_happens_before_number_matching():
    """A narrow NBSP inside 1 234,56 must be ASCII before the matcher runs."""
    html = "<p>Total: 1 234,56</p>"
    text, bands = normalize(html, STRICT)
    assert text == "total: 1230"
    assert bands == {"1 234,56": "1230"}


# ---------------------------------------------------------------------------
# Return contract
# ---------------------------------------------------------------------------


def test_returns_text_and_bands():
    text, bands = normalize("<p>Price 1234</p>", STRICT)
    assert isinstance(text, str)
    assert isinstance(bands, dict)
    assert bands == {"1234": "1230"}


def test_bands_is_empty_under_the_default_policy():
    text, bands = normalize("<p>Price 1234</p>", DEFAULT)
    assert bands == {}
    assert "1234" in text


def test_default_policy_redacts_timestamps_but_keeps_numbers():
    text, bands = normalize("<p>On 2024-04-12 we sold 1234 units.</p>", DEFAULT)
    assert text == "On [timestamp] we sold 1234 units."
    assert bands == {}


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_fixture_normalizes_under_every_policy(name):
    raw = fixture(name)
    for policy in (DEFAULT, STRICT, QUANTIZE, GRID):
        text, bands = normalize(raw, policy)
        assert text
        assert isinstance(bands, dict)


# ---------------------------------------------------------------------------
# Errors propagate; R4 forbids a partial result
# ---------------------------------------------------------------------------


def test_empty_input_raises():
    with pytest.raises(EmptyContent):
        normalize("", DEFAULT)


def test_content_with_no_text_raises():
    with pytest.raises(EmptyContent):
        normalize("<script>var x = 1;</script>", DEFAULT)


def test_oversized_input_raises():
    policy = DEFAULT.with_changes(max_content_bytes=10)
    with pytest.raises(ContentTooLarge):
        normalize("<p>" + "x" * 100 + "</p>", policy)


def test_binary_input_raises():
    with pytest.raises(NotTextual):
        normalize("<p>a\x00b</p>", DEFAULT)


# ---------------------------------------------------------------------------
# Idempotency, and precisely where it stops
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURE_NAMES)
@pytest.mark.parametrize(
    "policy,pid", [(DEFAULT, "default"), (STRICT, "strict")], ids=["default", "strict"]
)
def test_normalized_text_is_a_canonicalization_fixed_point(name, policy, pid):
    """Re-canonicalizing normalized text must be a no-op.

    This is what lets a validator re-check a leader's stored text without the
    second pass disagreeing with the first.
    """
    text, _ = normalize(fixture(name), policy)
    assert canonicalize_text(text, policy) == text


@pytest.mark.parametrize("name", FIXTURE_NAMES)
@pytest.mark.parametrize(
    "policy,pid", [(DEFAULT, "default"), (STRICT, "strict")], ids=["default", "strict"]
)
def test_re_running_the_stages_over_normalized_text_is_stable(name, policy, pid):
    """Under ``none`` and ``significant`` banding, the pipeline settles."""
    text, _ = normalize(fixture(name), policy)
    again = canonicalize_text(text, policy)
    again = quantize_timestamps(again, policy)
    again, _ = band_numbers(again, policy)
    assert again == text


def test_grid_banding_is_the_documented_non_idempotent_case():
    """The honest boundary, asserted rather than glossed over."""
    text, _ = normalize("<p>Price 1250</p>", GRID)
    assert text == "Price [1200~1300]"
    again, _ = band_numbers(text, GRID)
    assert again != text


# ---------------------------------------------------------------------------
# Convergence: the actual product claim
# ---------------------------------------------------------------------------


def test_two_captures_of_one_page_converge():
    """volatile_a and volatile_b differ only in volatile content."""
    a, _ = normalize(fixture("volatile_a.html"), STRICT)
    b, _ = normalize(fixture("volatile_b.html"), STRICT)
    assert a == b, "the whole thesis of the library"


def test_raw_bytes_of_those_two_captures_do_NOT_match():
    """The control: without WebAnchor there is nothing for strict_eq to agree on."""
    assert fixture("volatile_a.html") != fixture("volatile_b.html")


def test_cosmetic_rerenders_converge():
    """Smart quotes, NBSP, en dash, CRLF, ticking timestamp -- one output."""
    render_a = (
        "<p>Order “100418” — shipped 2024-04-12T08:15:22Z.</p>\r\n"
        "<p>Total: $1,234.56</p>"
    )
    render_b = (
        '<p>Order "100418" - shipped 2024-04-12T08:47:03Z.</p>\n'
        "<p>Total: $1,234.56</p>"
    )
    assert normalize(render_a, STRICT) == normalize(render_b, STRICT)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_output_is_identical_over_repeated_runs(name):
    raw = fixture(name)
    first = normalize(raw, STRICT)
    for _ in range(25):
        assert normalize(raw, STRICT) == first


def test_fresh_policy_objects_give_identical_output():
    raw = fixture("volatile_a.html")
    assert normalize(raw, Policy.strict()) == normalize(raw, Policy.strict())
