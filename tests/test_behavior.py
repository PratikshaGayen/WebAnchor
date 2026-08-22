"""R6: behavioral constants must reach policy_id, or divergence is silent.

The failure this guards against is the worst one available: two validators on
different WebAnchor versions, same bytes, one raises and one returns text, and
nothing in either result says why.
"""

import hashlib
import json
from decimal import Decimal

import pytest

from webanchor import Policy
from webanchor.behavior import BEHAVIOR_VERSION
from webanchor.errors import PolicyError


def test_behavior_version_is_a_positive_int():
    assert isinstance(BEHAVIOR_VERSION, int)
    assert not isinstance(BEHAVIOR_VERSION, bool)
    assert BEHAVIOR_VERSION >= 1


def test_behavior_version_is_in_canonical_json():
    data = json.loads(Policy.default().canonical_json())
    assert data["behavior_version"] == BEHAVIOR_VERSION


def _policy_id_for_behavior_version(policy, version):
    """Recompute the policy_id as if BEHAVIOR_VERSION were ``version``.

    Recomputed rather than monkeypatched: ``policy.to_dict`` reads the
    constant that ``policy.py`` imported at module load, so patching
    ``behavior.BEHAVIOR_VERSION`` would not be observed and the test would
    pass for the wrong reason -- it would prove nothing at all.
    """
    data = policy.to_dict()
    assert data["behavior_version"] == BEHAVIOR_VERSION
    data["behavior_version"] = version
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return "p1:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@pytest.mark.parametrize(
    "policy", [Policy.default(), Policy.strict()], ids=["default", "strict"]
)
def test_bumping_behavior_version_changes_policy_id(policy):
    """The whole point of R6, asserted directly."""
    bumped = _policy_id_for_behavior_version(policy, BEHAVIOR_VERSION + 1)
    assert bumped != policy.policy_id


def test_recomputation_with_the_real_version_reproduces_policy_id():
    """Guard on the guard: prove the recomputation above is faithful.

    Without this, ``test_bumping_behavior_version_changes_policy_id`` would
    still pass if the recomputation were subtly wrong -- any two different
    payloads hash differently.
    """
    policy = Policy.default()
    assert (
        _policy_id_for_behavior_version(policy, BEHAVIOR_VERSION) == policy.policy_id
    )


def test_every_behavior_version_value_gives_a_distinct_id():
    policy = Policy.default()
    seen = {
        _policy_id_for_behavior_version(policy, v) for v in range(1, 12)
    }
    assert len(seen) == 11


# ---------------------------------------------------------------------------
# The two constants R6 moved out of html_strip and onto Policy
# ---------------------------------------------------------------------------


def test_control_char_ratio_is_a_policy_field_and_reaches_policy_id():
    base = Policy.default()
    assert base.max_control_char_ratio == "0.05"
    changed = base.with_changes(max_control_char_ratio="0.5")
    assert changed.policy_id != base.policy_id


def test_max_tag_depth_is_a_policy_field_and_reaches_policy_id():
    base = Policy.default()
    assert base.max_tag_depth == 1000
    changed = base.with_changes(max_tag_depth=10)
    assert changed.policy_id != base.policy_id


@pytest.mark.parametrize(
    "value", ["-0.001", "1.001", "-1", "2", "100.0"]
)
def test_out_of_range_control_ratio_raises_policy_error(value):
    with pytest.raises(PolicyError):
        Policy(max_control_char_ratio=value)


@pytest.mark.parametrize("value", ["0", "0.0", "0.05", "0.5", "1.0", "1"])
def test_boundary_control_ratios_are_accepted(value):
    assert Policy(max_control_char_ratio=value).max_control_char_ratio == value


@pytest.mark.parametrize("value", [0, -1, -1000])
def test_non_positive_tag_depth_raises_policy_error(value):
    with pytest.raises(PolicyError):
        Policy(max_tag_depth=value)


def test_control_ratio_actually_changes_stripper_behavior():
    """Not just serialized -- the two thresholds really do disagree.

    This is the divergence R6 describes, reproduced in one process: identical
    bytes, one policy returns text, the other raises.
    """
    from webanchor.errors import NotTextual
    from webanchor.html_strip import strip_html

    html = "<p>" + "abcdefghij" * 10 + "\x01\x02\x03\x04\x05" + "</p>"
    lenient = Policy(max_control_char_ratio="0.5")
    strict_ratio = Policy(max_control_char_ratio="0.001")

    assert "abcdefghij" in strip_html(html, lenient)
    with pytest.raises(NotTextual):
        strip_html(html, strict_ratio)

    # ...and the disagreement is visible in the policy id, not silent.
    assert lenient.policy_id != strict_ratio.policy_id


def test_max_tag_depth_actually_changes_stripper_behavior():
    from webanchor.html_strip import strip_html

    html = "<div>" * 50 + "<script>ZZJSZZ</script>deep" + "</div>" * 50
    deep = Policy(max_tag_depth=1000)
    shallow = Policy(max_tag_depth=2)

    assert strip_html(html, deep) == "deep"
    # With a cap of 2 the parser cannot track the drop region it could not
    # push, so the script body survives -- different text, same bytes.
    assert strip_html(html, shallow) != strip_html(html, deep)
    assert deep.policy_id != shallow.policy_id


# ---------------------------------------------------------------------------
# The M4 correction: the last float in a numeric decision path is gone
# ---------------------------------------------------------------------------


def test_the_control_ratio_comparison_is_exact_and_a_float_would_diverge():
    """A boundary case where binary float and Decimal give OPPOSITE verdicts.

    100 characters, exactly 29 of them C0 controls, threshold ``0.29``. The
    exact answer is that the input is at the limit and therefore acceptable:
    29 is not greater than 29.

    In binary float, ``0.29 * 100`` is ``28.999999999999996``, so ``29 >
    28.999999999999996`` is true and the node *raises* instead. This is not a
    rounding artifact in a displayed number -- it is one validator returning a
    document and another rejecting it as binary, over identical bytes, with
    nothing in either result explaining the disagreement. That is precisely
    the silent asymmetric divergence R6 exists to prevent, which is why
    ``max_control_char_ratio`` is a Decimal string and the comparison is
    cross-multiplied in ``Decimal`` rather than divided in ``float``.
    """
    from webanchor.html_strip import reject_non_textual

    text = "" * 29 + "a" * 71
    assert len(text) == 100

    policy = Policy(max_control_char_ratio="0.29")

    # Exact arithmetic: at the limit, not over it. Must not raise.
    reject_non_textual(text, policy)

    # ...and the float path this replaced would have raised here.
    assert 29 > 0.29 * 100, "the float hazard this test pins has gone away"
    assert not Decimal(29) > Decimal("0.29") * Decimal(100)


def test_one_more_control_character_does_cross_the_line():
    """Guard on the guard: the threshold still rejects when it should."""
    from webanchor.errors import NotTextual
    from webanchor.html_strip import reject_non_textual

    text = "" * 30 + "a" * 70
    with pytest.raises(NotTextual):
        reject_non_textual(text, Policy(max_control_char_ratio="0.29"))


def test_a_float_ratio_is_refused_with_an_explanation_not_a_type_error():
    """The float branch exists for the MESSAGE, so the message is the test.

    Rejecting a float would happen anyway via the "must be a string" branch,
    but the resulting message would say ``got float`` and leave the caller to
    guess why a perfectly ordinary number is unacceptable. R3 is a rule
    somebody has to be able to follow, so the error names it and shows the
    fix.
    """
    with pytest.raises(PolicyError) as info:
        Policy(max_control_char_ratio=0.05)
    detail = info.value.detail
    assert "float" in detail
    assert "Decimal STRING" in detail
    assert "R3" in detail


def test_a_float_grid_step_is_refused_with_the_same_explanation():
    with pytest.raises(PolicyError) as info:
        Policy(number_band_mode="grid", number_grid_step=0.5)
    assert "float" in info.value.detail
    assert "R3" in info.value.detail


def test_no_policy_field_that_feeds_arithmetic_is_a_float():
    """Structural: R3 as a type invariant rather than an argued property."""
    import dataclasses

    policy = Policy.default()
    for field in dataclasses.fields(Policy):
        value = getattr(policy, field.name)
        assert not isinstance(value, float), field.name
