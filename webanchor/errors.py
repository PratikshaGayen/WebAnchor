"""Typed error taxonomy for WebAnchor.

Every failure path in WebAnchor raises a subclass of :class:`WebAnchorError`
(blueprint rule R4: fail loudly, never silently).  Errors carry a stable,
machine-readable ``code`` so that a contract can branch on the failure kind
without string-matching a human message.
"""

from typing import Optional

__all__ = [
    "WebAnchorError",
    "FetchError",
    "RateLimited",
    "UpstreamUnavailable",
    "NotFound",
    "Forbidden",
    "UnexpectedStatus",
    "NetworkError",
    "ContentError",
    "EmptyContent",
    "ContentTooLarge",
    "NotTextual",
    "BotWallDetected",
    "SoftErrorPage",
    "PolicyError",
    "UnstableContent",
    "PolicyMismatch",
    "ERROR_BY_CODE",
    "from_status",
]


class WebAnchorError(Exception):
    """Base class for every WebAnchor failure."""

    code: str = "webanchor.error"

    def __init__(self, detail: str = "", *, url: Optional[str] = None) -> None:
        self.detail: str = detail
        self.url: Optional[str] = url
        super().__init__(self._render())

    def _render(self) -> str:
        base = "[{0}] {1}".format(self.code, self.detail)
        if self.url:
            return "{0} (url={1})".format(base, self.url)
        return base

    def __str__(self) -> str:
        return self._render()

    def as_dict(self) -> dict[str, str]:
        """Calldata-safe primitive view of this error."""
        return {
            "code": self.code,
            "detail": self.detail,
            "url": self.url if self.url is not None else "",
        }


# --------------------------------------------------------------------------
# Fetch-layer failures: the HTTP round trip did not yield usable content.
# --------------------------------------------------------------------------


class FetchError(WebAnchorError):
    code = "fetch.error"


class RateLimited(FetchError):
    code = "fetch.rate_limited"


class UpstreamUnavailable(FetchError):
    code = "fetch.upstream_unavailable"


class NotFound(FetchError):
    code = "fetch.not_found"


class Forbidden(FetchError):
    code = "fetch.forbidden"


class UnexpectedStatus(FetchError):
    code = "fetch.unexpected_status"


class NetworkError(FetchError):
    code = "fetch.network"


# --------------------------------------------------------------------------
# Content-layer failures: bytes arrived, but they are not anchorable evidence.
# --------------------------------------------------------------------------


class ContentError(WebAnchorError):
    code = "content.error"


class EmptyContent(ContentError):
    code = "content.empty"


class ContentTooLarge(ContentError):
    code = "content.too_large"


class NotTextual(ContentError):
    code = "content.not_textual"


class BotWallDetected(ContentError):
    code = "content.bot_wall"


class SoftErrorPage(ContentError):
    code = "content.soft_error"


# --------------------------------------------------------------------------
# Policy-layer failures: normalization cannot yield a consensus-safe result.
# --------------------------------------------------------------------------


class PolicyError(WebAnchorError):
    code = "policy.error"


class UnstableContent(PolicyError):
    code = "policy.unstable"


class PolicyMismatch(PolicyError):
    code = "policy.mismatch"


def _walk_subclasses(root: type) -> list[type]:
    """Depth-first walk of every subclass of ``root``, ``root`` included."""
    found: list[type] = [root]
    seen: set[str] = {root.__qualname__}
    stack: list[type] = [root]
    while stack:
        current = stack.pop()
        for sub in current.__subclasses__():
            if sub.__qualname__ in seen:
                continue
            seen.add(sub.__qualname__)
            found.append(sub)
            stack.append(sub)
    return found


def _build_error_index() -> dict[str, type[WebAnchorError]]:
    index: dict[str, type[WebAnchorError]] = {}
    for cls in _walk_subclasses(WebAnchorError):
        code = cls.code
        existing = index.get(code)
        if existing is not None and existing is not cls:
            raise RuntimeError(
                "duplicate WebAnchor error code {0!r}: {1} and {2}".format(
                    code, existing.__name__, cls.__name__
                )
            )
        index[code] = cls
    return index


#: Every error code mapped to the class that owns it, derived programmatically.
ERROR_BY_CODE: dict[str, type[WebAnchorError]] = _build_error_index()


def from_status(status: int, *, url: Optional[str] = None) -> Optional[FetchError]:
    """Map an HTTP status code to the matching :class:`FetchError` instance.

    Returns ``None`` for 2xx, which is the only "no error" band.
    """
    if status == 429:
        return RateLimited("upstream rate limited the request", url=url)
    if status == 404:
        return NotFound("upstream returned 404", url=url)
    if status in (401, 403):
        return Forbidden("upstream refused the request ({0})".format(status), url=url)
    if 200 <= status <= 299:
        return None
    if 500 <= status <= 599:
        return UpstreamUnavailable(
            "upstream server error ({0})".format(status), url=url
        )
    return UnexpectedStatus("unexpected HTTP status {0}".format(status), url=url)
