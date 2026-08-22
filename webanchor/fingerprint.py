"""The stable content hash that ``strict_eq`` actually compares."""

import hashlib
import hmac

from .errors import EmptyContent, PolicyMismatch

__all__ = ["FINGERPRINT_VERSION", "fingerprint", "verify"]

FINGERPRINT_VERSION = "wa1"

_POLICY_ID_PREFIX = "p1:"


def _check_inputs(normalized_text: str, policy_id: str) -> None:
    if not policy_id.startswith(_POLICY_ID_PREFIX):
        raise PolicyMismatch(
            "policy_id must start with {0!r}, got {1!r}".format(
                _POLICY_ID_PREFIX, policy_id
            )
        )
    if not normalized_text or not normalized_text.strip():
        raise EmptyContent("cannot fingerprint empty or whitespace-only content")


def fingerprint(normalized_text: str, policy_id: str) -> str:
    """Return ``wa1:<sha256 hex>`` over the policy id and normalized text.

    The NUL byte between the two inputs is domain separation: without it,
    a policy id ending in one character and text starting with another could
    collide with a different (policy, text) split.
    """
    _check_inputs(normalized_text, policy_id)
    payload = policy_id.encode("utf-8") + b"\x00" + normalized_text.encode("utf-8")
    return "{0}:{1}".format(FINGERPRINT_VERSION, hashlib.sha256(payload).hexdigest())


def verify(normalized_text: str, policy_id: str, expected: str) -> bool:
    """Constant-time check that ``expected`` matches the recomputed fingerprint.

    A WebAnchor fingerprint is a CHECKSUM, not an authentication tag.

    A match proves exactly one thing: that the same normalized text and the
    same ``policy_id`` were hashed.  It proves nothing about *who* produced
    the value -- there is no key and no signature here, and every input is
    public.  In particular, it is not a defense against a malicious validator
    that runs the pipeline honestly over content which was itself manipulated
    upstream: garbage in, perfectly-matching fingerprints out.

    ``hmac.compare_digest`` is used so that no future refactor reintroduces a
    naive ``==`` comparison, not because there is a secret to protect.  Do not
    treat a verified fingerprint as a claim of authenticity or provenance.
    """
    actual = fingerprint(normalized_text, policy_id)
    return hmac.compare_digest(actual, expected)
