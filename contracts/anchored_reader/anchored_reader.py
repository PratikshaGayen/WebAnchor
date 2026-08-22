# { "Depends": "py-genlayer-multi:06zyvrlivjga0d5jlpdbprksc0pa6jmllxvp8s20hq1l512vh5yk" }
"""AnchoredWebReader -- the fix, using WebAnchor.

Same shape as ``contracts/naive_reader.py``, same public interface, same
``gl.eq_principle.strict_eq`` call. The only change is the fetch: instead of
handing raw response bytes straight to ``strict_eq``, the read is routed
through ``webanchor.anchor``, which strips volatile DOM, canonicalizes text,
quantizes/redacts timestamps, and emits a stable ``fingerprint`` that two
independent fetches of the same page actually agree on. That symmetry is the
pitch: WebAnchor does not require restructuring a contract, it requires one
import and one function call change.

``webanchor`` is vendored as a sibling package in this contract's own
directory (``contracts/anchored_reader/webanchor/``) rather than imported
from the repository root -- see the notice comment at the top of
``webanchor/__init__.py`` in this directory for why: current GenVM tooling
does not yet resolve a multi-file contract's imports against the repo root,
so the dependency is bundled alongside the contract instead.
"""

from genlayer import *

import webanchor


class AnchoredWebReader(gl.Contract):
    last_fingerprint: str
    last_policy_id: str
    last_url: str

    def __init__(self):
        self.last_fingerprint = ""
        self.last_policy_id = ""
        self.last_url = ""

    @gl.public.write
    def read_page(self, url: str) -> None:
        def fetch_anchored():
            evidence = webanchor.anchor(url, webanchor.Policy.default(), mode="get")
            return evidence.fingerprint

        self.last_fingerprint = gl.eq_principle.strict_eq(fetch_anchored)
        self.last_policy_id = webanchor.Policy.default().policy_id
        self.last_url = url

    @gl.public.view
    def get_last_fingerprint(self) -> str:
        return self.last_fingerprint

    @gl.public.view
    def get_last_policy_id(self) -> str:
        return self.last_policy_id

    @gl.public.view
    def get_last_url(self) -> str:
        return self.last_url
