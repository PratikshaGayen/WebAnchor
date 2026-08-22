"""Timestamp redaction and quantization -- including where the guarantee ends."""

import ast
import os

import pytest

from webanchor import Policy
from webanchor.errors import PolicyError
from webanchor.timestamps import (
    RELATIVE_TIME_TOKEN,
    TIMESTAMP_TOKEN,
    quantize_timestamps,
)

REDACT = Policy.default()
QUANTIZE = Policy.default().with_changes(timestamp_mode="quantize")
NONE = Policy.default().with_changes(timestamp_mode="none")
DAILY = QUANTIZE.with_changes(timestamp_quantum_seconds=86_400)
MINUTELY = QUANTIZE.with_changes(timestamp_quantum_seconds=60)


# ---------------------------------------------------------------------------
# Policy validation
# ---------------------------------------------------------------------------


def test_redact_is_the_default_mode():
    """Redaction is the only mode with a total guarantee, so it is the default."""
    assert Policy.default().timestamp_mode == "redact"


@pytest.mark.parametrize("mode", ["redact", "quantize", "none"])
def test_valid_modes_are_accepted(mode):
    assert Policy(timestamp_mode=mode).timestamp_mode == mode


@pytest.mark.parametrize(
    "mode", ["quantise", "REDACT", "Redact", "", "floor", "off", "true"]
)
def test_invalid_modes_raise_policy_error(mode):
    with pytest.raises(PolicyError) as info:
        Policy(timestamp_mode=mode)
    assert info.value.code == "policy.error"


def test_timestamp_mode_reaches_policy_id():
    base = Policy.default()
    assert base.with_changes(timestamp_mode="quantize").policy_id != base.policy_id
    assert base.with_changes(timestamp_mode="none").policy_id != base.policy_id


# ---------------------------------------------------------------------------
# Relative expressions -- always volatile, always replaced
# ---------------------------------------------------------------------------

RELATIVE_CASES = [
    "2 hours ago",
    "just now",
    "5 min ago",
    "yesterday",
    "a moment ago",
    "moments ago",
    "3 days ago",
    "1 second ago",
    "45 seconds ago",
    "10 mins ago",
    "an hour ago",
    "a minute ago",
    "2 weeks ago",
    "6 months ago",
    "1 year ago",
    "12 hrs ago",
]


@pytest.mark.parametrize("phrase", RELATIVE_CASES)
@pytest.mark.parametrize("policy,pid", [(REDACT, "redact"), (QUANTIZE, "quantize")],
                         ids=["redact", "quantize"])
def test_relative_expressions_are_replaced_in_both_modes(phrase, policy, pid):
    assert quantize_timestamps(phrase, policy) == RELATIVE_TIME_TOKEN


@pytest.mark.parametrize("phrase", RELATIVE_CASES)
def test_relative_expressions_survive_in_none_mode(phrase):
    assert quantize_timestamps(phrase, NONE) == phrase


def test_relative_expressions_are_replaced_inside_prose():
    assert quantize_timestamps("Updated 4 minutes ago by staff", REDACT) == (
        "Updated " + RELATIVE_TIME_TOKEN + " by staff"
    )


def test_relative_expressions_are_case_insensitive():
    assert quantize_timestamps("Just Now", REDACT) == RELATIVE_TIME_TOKEN
    assert quantize_timestamps("YESTERDAY", REDACT) == RELATIVE_TIME_TOKEN


def test_all_relative_phrasings_converge_on_one_token():
    """The whole point: "2 hours ago" and "3 hours ago" must stop differing."""
    outputs = {quantize_timestamps(p, REDACT) for p in RELATIVE_CASES}
    assert outputs == {RELATIVE_TIME_TOKEN}


def test_relative_expressions_are_not_resolved_to_absolute_times():
    """Resolving needs "now", and "now" differs per validator."""
    result = quantize_timestamps("2 hours ago", QUANTIZE)
    assert result == RELATIVE_TIME_TOKEN
    assert "T" not in result and "Z" not in result


def test_bare_today_is_deliberately_not_treated_as_relative():
    """It is far more often an ordinary word than a timestamp."""
    assert quantize_timestamps("40% off today only", REDACT) == "40% off today only"


def test_relative_matching_respects_word_boundaries():
    assert quantize_timestamps("yesterdayish", REDACT) == "yesterdayish"
    assert quantize_timestamps("pre-yesterday", REDACT) == "pre-yesterday"


# ---------------------------------------------------------------------------
# Absolute timestamps: detection across formats
# ---------------------------------------------------------------------------

ABSOLUTE_CASES = [
    ("2024-04-12T08:15:22Z", "2024-04-12T08:00:00Z"),
    ("2024-04-12T08:15:22", "2024-04-12T08:00:00Z"),
    ("2024-04-12T08:15:22.123Z", "2024-04-12T08:00:00Z"),
    ("2024-04-12T08:15:22+00:00", "2024-04-12T08:00:00Z"),
    ("2024-04-12T08:15:22+02:00", "2024-04-12T06:00:00Z"),
    ("2024-04-12T08:15:22-0500", "2024-04-12T13:00:00Z"),
    ("2024-04-12 08:15:22", "2024-04-12T08:00:00Z"),
    ("2024-04-12", "2024-04-12T00:00:00Z"),
    ("Fri, 12 Apr 2024 08:15:22 GMT", "2024-04-12T08:00:00Z"),
    ("Fri, 12 Apr 2024 08:15:22 UTC", "2024-04-12T08:00:00Z"),
    ("12 April 2024", "2024-04-12T00:00:00Z"),
    ("12 Apr 2024", "2024-04-12T00:00:00Z"),
    ("April 12, 2024", "2024-04-12T00:00:00Z"),
    ("Apr 12 2024", "2024-04-12T00:00:00Z"),
    ("12th April 2024", "2024-04-12T00:00:00Z"),
    ("September 1, 2024", "2024-09-01T00:00:00Z"),
    ("Sept 1, 2024", "2024-09-01T00:00:00Z"),
]


@pytest.mark.parametrize("text,expected", ABSOLUTE_CASES, ids=[c[0] for c in ABSOLUTE_CASES])
def test_absolute_timestamps_quantize_to_one_canonical_form(text, expected):
    assert quantize_timestamps(text, QUANTIZE) == expected


@pytest.mark.parametrize("text,expected", ABSOLUTE_CASES, ids=[c[0] for c in ABSOLUTE_CASES])
def test_absolute_timestamps_redact(text, expected):
    assert quantize_timestamps(text, REDACT) == TIMESTAMP_TOKEN


@pytest.mark.parametrize("text,expected", ABSOLUTE_CASES, ids=[c[0] for c in ABSOLUTE_CASES])
def test_none_mode_leaves_absolute_timestamps_alone(text, expected):
    assert quantize_timestamps(text, NONE) == text


def test_every_format_of_the_same_instant_converges_under_quantize():
    """Format divergence, eliminated: seven spellings, one output."""
    same_day = [
        "2024-04-12T08:15:22Z",
        "2024-04-12 08:15:22",
        "Fri, 12 Apr 2024 08:15:22 GMT",
        "2024-04-12T08:59:59Z",
        "2024-04-12T08:00:00Z",
    ]
    assert {quantize_timestamps(t, QUANTIZE) for t in same_day} == {
        "2024-04-12T08:00:00Z"
    }


def test_rfc1123_is_matched_whole_not_partially_eaten():
    """The day-month-year pattern must not steal "12 Apr 2024" from the RFC form."""
    result = quantize_timestamps("Fri, 12 Apr 2024 08:15:22 GMT", QUANTIZE)
    assert result == "2024-04-12T08:00:00Z"
    assert "GMT" not in result and "Fri" not in result


def test_timestamps_are_replaced_inside_prose():
    assert quantize_timestamps("Shipped on 12 April 2024 by us.", REDACT) == (
        "Shipped on " + TIMESTAMP_TOKEN + " by us."
    )


@pytest.mark.parametrize(
    "text", ["2024-02-31", "2024-13-01", "2024-00-10", "9999-99-99", "1234-56-78"]
)
def test_invalid_dates_are_left_untouched_not_raised_on(text):
    """A footer typo must not abort normalization of the whole page."""
    assert quantize_timestamps(text, REDACT) == text
    assert quantize_timestamps(text, QUANTIZE) == text


@pytest.mark.parametrize("text", ["version 2024 build", "10-20-30", "part 1-2-3"])
def test_non_timestamps_are_not_matched(text):
    assert quantize_timestamps(text, REDACT) == text


def test_dates_without_a_time_component_floor_to_midnight_and_are_stable():
    """A bare date has no sub-day volatility to begin with."""
    for quantum in (60, 3600, 86_400):
        policy = QUANTIZE.with_changes(timestamp_quantum_seconds=quantum)
        assert quantize_timestamps("2024-04-12", policy) == "2024-04-12T00:00:00Z"


def test_quantum_size_controls_the_bucket():
    text = "2024-04-12T08:15:22Z"
    assert quantize_timestamps(text, MINUTELY) == "2024-04-12T08:15:00Z"
    assert quantize_timestamps(text, QUANTIZE) == "2024-04-12T08:00:00Z"
    assert quantize_timestamps(text, DAILY) == "2024-04-12T00:00:00Z"


def test_pre_epoch_timestamps_floor_downward_not_toward_zero():
    """Flooring must go toward minus infinity on both sides of 1970."""
    assert quantize_timestamps("1969-12-31T23:15:00Z", QUANTIZE) == (
        "1969-12-31T23:00:00Z"
    )


# ---------------------------------------------------------------------------
# THE LIMITATION. Quantization reduces divergence; it does not remove it.
# ---------------------------------------------------------------------------


def test_quantization_does_not_eliminate_boundary_straddle():
    """Two fetches five seconds apart across a bucket edge STILL DIVERGE.

    This asserts the failure, deliberately.  Quantization lowers the residual
    divergence risk to roughly ``spread / quantum``; it never reaches zero,
    and a leader at 08:59:58 with a validator at 09:00:03 is the case where
    the guarantee does not hold.  Pretending otherwise would be the most
    dangerous thing this library could do, because the failure is rare enough
    to survive testing and frequent enough to break production consensus.
    """
    leader = "Generated at 2024-04-12T08:59:58Z"
    validator = "Generated at 2024-04-12T09:00:03Z"

    leader_out = quantize_timestamps(leader, QUANTIZE)
    validator_out = quantize_timestamps(validator, QUANTIZE)

    assert leader_out == "Generated at 2024-04-12T08:00:00Z"
    assert validator_out == "Generated at 2024-04-12T09:00:00Z"
    assert leader_out != validator_out, (
        "quantization is expected to DIVERGE across a bucket edge; if this "
        "assertion ever fails the documented risk model is wrong"
    )


def test_a_larger_quantum_shrinks_but_never_closes_the_straddle_window():
    """Raising the quantum moves the edge; it does not delete it."""
    a, b = "2024-04-12T23:59:58Z", "2024-04-13T00:00:03Z"

    # An hourly quantum diverges here...
    assert quantize_timestamps(a, QUANTIZE) != quantize_timestamps(b, QUANTIZE)
    # ...a daily one also diverges here, because this pair straddles ITS edge
    # too.  Every quantum has edges; only the count of them changes.
    assert quantize_timestamps(a, DAILY) != quantize_timestamps(b, DAILY)


def test_redaction_has_no_straddle_case_at_all():
    """Why redact is the default: the residual risk is exactly zero."""
    pairs = [
        ("2024-04-12T08:59:58Z", "2024-04-12T09:00:03Z"),
        ("2024-04-12T23:59:59Z", "2024-04-13T00:00:00Z"),
        ("2024-04-12T08:15:22Z", "2019-01-01T00:00:00Z"),
        ("12 April 2024", "Fri, 13 Sep 2019 08:15:22 GMT"),
    ]
    for leader, validator in pairs:
        assert quantize_timestamps(leader, REDACT) == quantize_timestamps(
            validator, REDACT
        ) == TIMESTAMP_TOKEN


def test_within_bucket_fetches_do_converge():
    """The other half of the honest picture: it works most of the time."""
    a = quantize_timestamps("2024-04-12T08:00:01Z", QUANTIZE)
    b = quantize_timestamps("2024-04-12T08:59:59Z", QUANTIZE)
    assert a == b == "2024-04-12T08:00:00Z"


def test_the_module_documents_the_risk_formula():
    """The limitation must be in the docstring, not only in this file."""
    import webanchor.timestamps as module

    doc = module.__doc__ or ""
    assert "quantum" in doc
    assert "does not" in doc.lower() or "not eliminate" in doc.lower()
    assert "redact" in doc.lower()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_no_wall_clock_is_read_anywhere_in_this_module():
    """Reading "now" would make every validator's output different."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "webanchor",
        "timestamps.py",
    )
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    banned = {"now", "utcnow", "today", "timestamp", "fromtimestamp"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in banned, "{0} at line {1}".format(
                node.attr, node.lineno
            )


@pytest.mark.parametrize(
    "policy,pid",
    [(REDACT, "r"), (QUANTIZE, "q"), (NONE, "n")],
    ids=["redact", "quantize", "none"],
)
def test_output_is_identical_over_repeated_runs(policy, pid):
    text = (
        "Posted 2024-04-12T08:15:22Z, updated 4 minutes ago, "
        "reviewed Fri, 12 Apr 2024 08:15:22 GMT, published 12 April 2024."
    )
    first = quantize_timestamps(text, policy)
    for _ in range(50):
        assert quantize_timestamps(text, policy) == first


def test_redaction_is_idempotent():
    text = "Posted 2024-04-12T08:15:22Z and updated 4 minutes ago"
    once = quantize_timestamps(text, REDACT)
    assert quantize_timestamps(once, REDACT) == once


def test_quantization_is_idempotent():
    """A canonical rendering must re-quantize to itself, or re-anchoring drifts."""
    text = "Posted 2024-04-12T08:15:22Z and updated 4 minutes ago"
    once = quantize_timestamps(text, QUANTIZE)
    assert once == "Posted 2024-04-12T08:00:00Z and updated [relative-time]"
    assert quantize_timestamps(once, QUANTIZE) == once
