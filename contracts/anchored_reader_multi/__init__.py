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

This is the LIVE-DEPLOYABLE sibling of ``contracts/anchored_reader/`` (which
stays as-is for the fast, no-network ``tests/direct/`` proof). It differs
from that copy in two ways forced by deploying to a REAL GenLayer Studio
node via ``gltest``'s multi-file packaging path, both confirmed the hard way
against the live network (see the integration report for exact commands,
transaction hashes, and tracebacks):

1. ``webanchor`` here is ONE FLAT MODULE (``webanchor.py``), not a
   subpackage directory. GenVM's ``py-genlayer-multi`` runner does not
   correctly support a nested subpackage (a directory with its own
   ``__init__.py``) inside a deployed contract archive -- a live deploy with
   ``webanchor/`` as a real subpackage returned
   ``AttributeError: module 'contract.webanchor' has no attribute 'anchor'``
   (the submodule's own body silently never ran), and an isolated two-line
   repro of the same nested-package shape independently reproduced
   ``ImportError: cannot import name 'x' from partially initialized module
   'contract' (most likely due to a circular import)``. A FLAT sibling
   module worked immediately in the same repro. ``webanchor.py`` in this
   directory is therefore a mechanical, order-respecting concatenation of
   the real ``webanchor/`` package's submodules -- see its own header
   comment for the exact procedure and the real source of truth.
2. The import below is ``from . import webanchor``, not a bare
   ``import webanchor``: this file is packaged as ``contract/__init__.py``
   (py-genlayer-multi's convention), and only the archive's root directory
   ends up on ``sys.path`` -- not ``/contract`` itself -- so a bare
   ``import webanchor`` fails with
   ``ModuleNotFoundError: No module named 'webanchor'`` even with
   ``webanchor.py`` sitting right next to this file. The package-relative
   import is the fix.
"""

from genlayer import *

from . import webanchor


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
