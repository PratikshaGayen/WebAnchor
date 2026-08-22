"""Evidence: the calldata-safe result object handed back to a contract."""

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

__all__ = ["Evidence"]


@dataclass(frozen=True, eq=True, init=False)
class Evidence:
    """An anchored web read.

    ``fingerprint`` is what validators compare; ``text`` is what the LLM reads.
    The two are kept separate on purpose -- shipping full page text through
    consensus calldata would bloat every transaction for no added guarantee.
    """

    url: str
    status: int
    fingerprint: str
    policy_id: str
    text: str
    fetched_bucket: int
    _bands: dict[str, str] = field(repr=False)

    def __init__(
        self,
        url: str,
        status: int,
        fingerprint: str,
        policy_id: str,
        text: str,
        bands: Optional[Mapping[str, str]] = None,
        fetched_bucket: int = 0,
    ) -> None:
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "fingerprint", fingerprint)
        object.__setattr__(self, "policy_id", policy_id)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "fetched_bucket", fetched_bucket)
        object.__setattr__(self, "_bands", dict(bands) if bands else {})

    @property
    def bands(self) -> dict[str, str]:
        """A shallow copy -- mutating it cannot corrupt this Evidence."""
        return dict(self._bands)

    def __len__(self) -> int:
        return len(self.text)

    def to_calldata(self, *, include_text: bool = False) -> dict[str, Any]:
        """Deterministically-keyed dict of calldata-safe primitives."""
        payload: dict[str, Any] = {
            "bands": dict(self._bands),
            "fetched_bucket": self.fetched_bucket,
            "fingerprint": self.fingerprint,
            "policy_id": self.policy_id,
            "status": self.status,
            "url": self.url,
        }
        if include_text:
            payload["text"] = self.text
        return {key: payload[key] for key in sorted(payload)}

    def summary(self) -> str:
        return "Evidence({0} status={1} {2} chars={3} bands={4})".format(
            self.url,
            self.status,
            self.fingerprint,
            len(self.text),
            len(self._bands),
        )
