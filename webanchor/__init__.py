"""WebAnchor -- turn a web read into a ``strict_eq``-able fact.

This package's core is pure stdlib and imports cleanly with no GenLayer
runtime present (blueprint rule R2).  Nothing here may import
``webanchor.fetch``, which is the only module allowed to touch ``genlayer``.
"""

from .behavior import BEHAVIOR_VERSION
from .errors import (
    ERROR_BY_CODE,
    BotWallDetected,
    ContentError,
    ContentTooLarge,
    EmptyContent,
    FetchError,
    Forbidden,
    NetworkError,
    NotFound,
    NotTextual,
    PolicyError,
    PolicyMismatch,
    RateLimited,
    SoftErrorPage,
    UnexpectedStatus,
    UnstableContent,
    UpstreamUnavailable,
    WebAnchorError,
    from_status,
)
from .evidence import Evidence
from .fingerprint import FINGERPRINT_VERSION, fingerprint, verify
from .html_strip import strip_html
from .numbers import band_numbers
from .pipeline import anchor, anchor_html, normalize
from .policy import Policy
from .text import canonicalize_text
from .timestamps import quantize_timestamps

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Policy",
    "Evidence",
    "BEHAVIOR_VERSION",
    "anchor",
    "anchor_html",
    "normalize",
    "strip_html",
    "canonicalize_text",
    "quantize_timestamps",
    "band_numbers",
    "fingerprint",
    "verify",
    "FINGERPRINT_VERSION",
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
