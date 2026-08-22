"""Fingerprint stability, domain separation, and failure modes."""

import pytest

from webanchor import Policy, fingerprint, verify
from webanchor.errors import EmptyContent, PolicyMismatch
from webanchor.fingerprint import FINGERPRINT_VERSION

PID = Policy.default().policy_id
STRICT_PID = Policy.strict().policy_id
TEXT = "The quick brown fox jumps over the lazy dog."


def test_shape():
    fp = fingerprint(TEXT, PID)
    assert fp.startswith(FINGERPRINT_VERSION + ":")
    hex_part = fp.split(":", 1)[1]
    assert len(hex_part) == 64
    assert all(c in "0123456789abcdef" for c in hex_part)


def test_same_inputs_same_fingerprint():
    assert fingerprint(TEXT, PID) == fingerprint(TEXT, PID)


def test_different_text_different_fingerprint():
    assert fingerprint(TEXT, PID) != fingerprint(TEXT + "!", PID)


def test_different_policy_different_fingerprint():
    assert PID != STRICT_PID
    assert fingerprint(TEXT, PID) != fingerprint(TEXT, STRICT_PID)


def test_domain_separation_prevents_boundary_collision():
    # Without the NUL separator these two would hash identical bytes.
    a = fingerprint("bc", "p1:a")
    b = fingerprint("c", "p1:ab")
    assert a != b


@pytest.mark.parametrize(
    "text",
    [
        "café résumé naïve",
        "你好世界",
        "emoji \U0001f600 tail",
        "é vs é",
    ],
)
def test_unicode_round_trips_deterministically(text):
    first = fingerprint(text, PID)
    second = fingerprint(str(text), PID)
    assert first == second
    assert verify(text, PID, first)


def test_unicode_distinct_forms_are_distinct_here():
    # fingerprint() does not normalize; that is the canonicalizer's job (M3).
    precomposed = "é"
    decomposed = "é"
    assert precomposed != decomposed
    assert fingerprint(precomposed, PID) != fingerprint(decomposed, PID)


@pytest.mark.parametrize("text", ["", " ", "\n", "\t\n  \r", "     "])
def test_empty_or_whitespace_raises(text):
    with pytest.raises(EmptyContent) as info:
        fingerprint(text, PID)
    assert info.value.code == "content.empty"


@pytest.mark.parametrize("pid", ["p2:abc", "abc", "", "P1:abc", "p1", " p1:abc"])
def test_bad_policy_prefix_raises(pid):
    with pytest.raises(PolicyMismatch) as info:
        fingerprint(TEXT, pid)
    assert info.value.code == "policy.mismatch"


def test_policy_check_precedes_content_check():
    with pytest.raises(PolicyMismatch):
        fingerprint("", "nope")


def test_verify_true():
    assert verify(TEXT, PID, fingerprint(TEXT, PID)) is True


def test_verify_false_on_wrong_text():
    assert verify("other text", PID, fingerprint(TEXT, PID)) is False


def test_verify_false_on_wrong_policy():
    assert verify(TEXT, STRICT_PID, fingerprint(TEXT, PID)) is False


def test_verify_false_on_garbage_expected():
    assert verify(TEXT, PID, "wa1:" + "0" * 64) is False
    assert verify(TEXT, PID, "") is False


def test_verify_propagates_input_errors():
    with pytest.raises(EmptyContent):
        verify("   ", PID, "wa1:whatever")


def test_verify_docstring_says_checksum_not_authentication():
    """Guard the warning itself: a later milestone must not read verify() as auth."""
    doc = verify.__doc__ or ""
    assert "CHECKSUM, not an authentication tag" in doc
    assert "who" in doc
