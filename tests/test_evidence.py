"""Evidence: calldata safety, deterministic keys, and defensive copying."""

import dataclasses

import pytest

from webanchor import Evidence, Policy, fingerprint

PID = Policy.default().policy_id
TEXT = "Widget 9000 costs 104.50 EUR and ships in two days."


def make(bands=None, text=TEXT):
    return Evidence(
        url="https://example.com/product/42",
        status=200,
        fingerprint=fingerprint(text, PID),
        policy_id=PID,
        text=text,
        bands=bands if bands is not None else {},
        fetched_bucket=1_700_000_000,
    )


def test_to_calldata_excludes_text_by_default():
    data = make().to_calldata()
    assert "text" not in data
    assert set(data) == {
        "bands",
        "fetched_bucket",
        "fingerprint",
        "policy_id",
        "status",
        "url",
    }


def test_to_calldata_includes_text_when_asked():
    data = make().to_calldata(include_text=True)
    assert data["text"] == TEXT


def test_to_calldata_values_are_calldata_safe():
    data = make({"price": "100.00-110.00"}).to_calldata(include_text=True)
    for key, value in data.items():
        assert isinstance(key, str)
        assert isinstance(value, (str, int, dict)), key
        if isinstance(value, dict):
            for k, v in value.items():
                assert isinstance(k, str)
                assert isinstance(v, str)
    assert isinstance(data["status"], int)
    assert isinstance(data["fetched_bucket"], int)
    assert not isinstance(data["status"], bool)


def test_to_calldata_keys_are_sorted():
    for include in (False, True):
        keys = list(make().to_calldata(include_text=include))
        assert keys == sorted(keys)


def test_to_calldata_is_stable_across_calls():
    ev = make({"price": "1-2", "stock": "10-20"})
    assert ev.to_calldata() == ev.to_calldata()
    assert list(ev.to_calldata()["bands"]) == ["price", "stock"]


def test_bands_property_returns_a_copy():
    ev = make({"price": "100.00-110.00"})
    got = ev.bands
    got["price"] = "TAMPERED"
    got["injected"] = "x"
    assert ev.bands == {"price": "100.00-110.00"}
    assert "injected" not in ev.bands
    assert ev.to_calldata()["bands"] == {"price": "100.00-110.00"}


def test_source_dict_mutation_does_not_leak_in():
    source = {"price": "1-2"}
    ev = Evidence(
        url="https://example.com",
        status=200,
        fingerprint=fingerprint(TEXT, PID),
        policy_id=PID,
        text=TEXT,
        bands=source,
        fetched_bucket=0,
    )
    source["price"] = "9-9"
    assert ev.bands == {"price": "1-2"}


def test_calldata_bands_mutation_does_not_leak():
    ev = make({"price": "1-2"})
    data = ev.to_calldata()
    data["bands"]["price"] = "nope"
    assert ev.bands == {"price": "1-2"}


def test_bands_default_is_empty_in_m1():
    assert make().bands == {}


def test_len_is_text_length():
    ev = make()
    assert len(ev) == len(TEXT)


def test_summary_is_one_short_line():
    line = make().summary()
    assert "\n" not in line
    assert "https://example.com/product/42" in line
    assert len(line) < 200


def test_evidence_is_frozen():
    ev = make()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.status = 500


def test_equality_by_value():
    assert make() == make()
    assert make({"a": "1"}) != make({"a": "2"})
    assert make(text=TEXT) != make(text=TEXT + " extra")
