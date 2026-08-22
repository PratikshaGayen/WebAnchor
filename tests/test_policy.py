"""Policy validation, immutability, and deterministic policy_id derivation."""

import dataclasses
import hashlib

import pytest

from webanchor import Policy
from webanchor.policy import POLICY_ID_HEX_LEN
from webanchor.errors import PolicyError

# Regression lock. If this literal changes, every previously-anchored
# fingerprint in the wild is invalidated -- that must be a deliberate act.
DEFAULT_POLICY_ID = "p1:93ae77e034942b7acf8ac9bf3e4a718c"
STRICT_POLICY_ID = "p1:9f2d5ca046dda774bc68d4c2e22cca9a"

FIELD_MUTATIONS = {
    "schema_version": 2,
    "strip_scripts": False,
    "strip_styles": False,
    "strip_comments": False,
    "strip_ad_containers": True,
    "ad_container_patterns": ("ad",),
    "extra_ad_container_patterns": ("housead",),
    "volatile_attrs": ("nonce",),
    "unicode_form": "NFC",
    "collapse_whitespace": False,
    "lowercase": True,
    "number_band_mode": "grid",
    "number_grid_step": "25",
    "number_significant_digits": 4,
    "timestamp_mode": "quantize",
    "timestamp_quantum_seconds": 60,
    "max_content_bytes": 1_000,
    "max_tag_depth": 50,
    "max_control_char_ratio": "0.5",
    "detect_bot_wall_server_hint": True,
}


def test_default_policy_id_is_locked():
    assert Policy.default().policy_id == DEFAULT_POLICY_ID


def test_strict_policy_id_is_locked():
    assert Policy.strict().policy_id == STRICT_POLICY_ID


def test_policy_id_has_expected_shape():
    pid = Policy.default().policy_id
    assert pid.startswith("p1:")
    hex_part = pid[3:]
    assert len(hex_part) == POLICY_ID_HEX_LEN
    assert all(c in "0123456789abcdef" for c in hex_part)


@pytest.mark.parametrize(
    "policy",
    [Policy.default(), Policy.strict(), Policy(lowercase=True, max_content_bytes=17)],
    ids=["default", "strict", "custom"],
)
def test_policy_id_keeps_128_bits_of_entropy(policy):
    """Regression lock against a future truncation.

    64 bits is brute-forceable in minutes; a colliding policy_id would let two
    validators normalize differently while agreeing on a fingerprint.
    """
    assert POLICY_ID_HEX_LEN == 32
    hex_part = policy.policy_id[len("p1:"):]
    assert len(hex_part) == 32, "policy_id must retain 128 bits of sha256"


def test_policy_id_is_a_true_sha256_prefix():
    p = Policy.default()
    full = hashlib.sha256(p.canonical_json().encode("utf-8")).hexdigest()
    assert p.policy_id == "p1:" + full[:32]


def test_mutation_table_covers_every_field():
    names = {f.name for f in dataclasses.fields(Policy)}
    assert names == set(FIELD_MUTATIONS)


@pytest.mark.parametrize("name,value", sorted(FIELD_MUTATIONS.items()))
def test_changing_any_field_changes_policy_id(name, value):
    base = Policy.default()
    assert getattr(base, name) != value, "mutation must actually differ"
    changed = dataclasses.replace(base, **{name: value})
    assert changed.policy_id != base.policy_id


def test_identical_policies_share_policy_id():
    a = Policy()
    b = Policy(
        schema_version=1,
        strip_scripts=True,
        strip_styles=True,
        strip_comments=True,
        strip_ad_containers=False,
        ad_container_patterns=(
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
        ),
        extra_ad_container_patterns=(),
        volatile_attrs=(
            "nonce",
            "integrity",
            "csrf",
            "data-session",
            "data-request-id",
            "data-timestamp",
            "data-nonce",
        ),
        unicode_form="NFKC",
        collapse_whitespace=True,
        lowercase=False,
        number_band_mode="none",
        number_grid_step="1",
        number_significant_digits=3,
        timestamp_mode="redact",
        timestamp_quantum_seconds=3600,
        max_content_bytes=2_000_000,
        max_tag_depth=1000,
        max_control_char_ratio="0.05",
        detect_bot_wall_server_hint=False,
    )
    assert a is not b
    assert a.policy_id == b.policy_id
    assert a == b


def test_policy_id_is_stable_across_repeated_calls():
    p = Policy.default()
    assert p.policy_id == p.policy_id == Policy.default().policy_id


def test_strict_policy_settings():
    s = Policy.strict()
    assert s.number_band_mode == "significant"
    assert s.timestamp_quantum_seconds == 86_400
    assert s.lowercase is True
    assert s.strip_ad_containers is True
    assert s.detect_bot_wall_server_hint is True
    assert s.policy_id != Policy.default().policy_id


def test_default_policy_has_server_hint_detector_off():
    """Opt-in only -- see BENCHMARK.md's Cloudflare-branch finding for why."""
    assert Policy.default().detect_bot_wall_server_hint is False


def test_default_policy_is_conservative_about_ad_containers():
    """Silent content loss is the failure mode this library exists to prevent.

    Aggressive class-name heuristics eat real content on sites that use
    generic names like ``promo``; that has to be opt-in, never the default.
    """
    assert Policy.default().strip_ad_containers is False
    assert Policy.default().ad_container_patterns[0] == "ad"


def test_to_dict_is_primitives_only():
    data = Policy.default().to_dict()
    assert isinstance(data["volatile_attrs"], list)
    assert isinstance(data["ad_container_patterns"], list)
    for value in data.values():
        assert isinstance(value, (int, float, str, bool, list))
    for item in data["volatile_attrs"]:
        assert isinstance(item, str)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"number_band_mode": "banded"},
        {"number_band_mode": ""},
        {"unicode_form": "NFKX"},
        {"unicode_form": "nfkc"},
        {"timestamp_quantum_seconds": 0},
        {"timestamp_quantum_seconds": -1},
        {"max_content_bytes": 0},
        {"max_content_bytes": -5},
        {"number_band_mode": "percent"},
        {"number_grid_step": "0"},
        {"number_grid_step": "-1"},
        {"number_grid_step": "not a number"},
        {"number_grid_step": "NaN"},
        {"number_grid_step": "Infinity"},
        {"number_grid_step": 1},
        {"number_grid_step": 0.5},
        {"timestamp_mode": "quantise"},
        {"timestamp_mode": ""},
        {"timestamp_mode": "REDACT"},
        {"max_tag_depth": 0},
        {"max_tag_depth": -1},
        {"max_control_char_ratio": "-0.01"},
        {"max_control_char_ratio": "1.01"},
        {"max_control_char_ratio": 0.05},
        {"max_control_char_ratio": "five percent"},
        {"max_control_char_ratio": "NaN"},
    ],
)
def test_invalid_values_raise_policy_error(kwargs):
    with pytest.raises(PolicyError):
        Policy(**kwargs)


def test_policy_error_is_not_value_error():
    with pytest.raises(PolicyError) as info:
        Policy(number_band_mode="nope")
    assert not isinstance(info.value, ValueError)
    assert info.value.code == "policy.error"


def test_policy_is_frozen():
    p = Policy.default()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.lowercase = True


def test_replace_validates():
    with pytest.raises(PolicyError):
        dataclasses.replace(Policy.default(), unicode_form="XXX")


def test_canonical_json_is_sorted_and_compact():
    text = Policy.default().canonical_json()
    assert ", " not in text
    assert text.startswith('{"ad_container_patterns"')
