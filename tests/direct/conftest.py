"""Test-local fixups for ``genlayer-test``'s direct-mode pytest plugin.

``gltest.direct.loader._inject_message_to_fd0`` (genlayer-test 0.29.2, the
latest published version at the time of writing) creates a temp file, dup2()s
it onto fd 0, then calls ``os.unlink(path)`` while that duplicated descriptor
is still open. On POSIX this is the standard "create, dup, unlink" idiom and
works because POSIX allows deleting a file that a process still has open. On
Windows there is no such guarantee: ``os.unlink`` while a duplicate handle to
the same file is open raises ``PermissionError: [WinError 32] The process
cannot access the file because it is being used by another process`` --
confirmed by reproducing it directly against the installed package before
writing this fixup. This is a genuine upstream bug in the direct-mode plugin
on Windows, not something in WebAnchor's contracts or tests.

This conftest monkeypatches the one function at fault with a copy that is
byte-for-byte identical except the final ``os.unlink`` is wrapped in a
``try/except OSError`` -- the temp file is still created and still injected
into fd 0 exactly as upstream does it; only the (best-effort, POSIX-only)
cleanup is made non-fatal. Nothing about message encoding, fd redirection, or
contract semantics is changed. Leaked temp files are the acceptable cost of
making the plugin usable on Windows at all; the OS temp directory is cleaned
periodically regardless.

If a future genlayer-test release fixes this upstream, this monkeypatch
becomes a no-op wrapper around correct behavior and can be deleted.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

from gltest.direct import loader as _gl_loader


def _inject_message_to_fd0_windows_safe(vm) -> None:
    """Copy of ``gltest.direct.loader._inject_message_to_fd0`` with a
    Windows-safe (non-fatal) unlink. See module docstring for why this
    exists."""
    try:
        from genlayer.py import calldata
        from genlayer.py.types import Address
    except ImportError:
        return

    sender_addr = vm.sender
    if isinstance(sender_addr, bytes):
        sender_addr = Address(sender_addr)

    contract_addr = vm._contract_address
    if isinstance(contract_addr, bytes):
        contract_addr = Address(contract_addr)

    origin_addr = vm.origin
    if isinstance(origin_addr, bytes):
        origin_addr = Address(origin_addr)

    message_data = {
        "contract_address": contract_addr,
        "sender_address": sender_addr,
        "origin_address": origin_addr,
        "stack": [],
        "value": vm._value,
        "datetime": vm._datetime,
        "is_init": False,
        "chain_id": vm._chain_id,
        "entry_kind": 0,
        "entry_data": b"",
        "entry_stage_data": None,
    }

    encoded = calldata.encode(message_data)

    fd, path = tempfile.mkstemp()
    try:
        os.write(fd, encoded)
        os.lseek(fd, 0, os.SEEK_SET)

        original_stdin = os.dup(0)
        vm._original_stdin_fd = original_stdin

        os.dup2(fd, 0)
    finally:
        os.close(fd)
        try:
            os.unlink(path)
        except OSError:
            # Windows: fd 0 still references this file via dup2(); the OS
            # will reclaim it from the temp directory later. See module
            # docstring.
            pass


_gl_loader._inject_message_to_fd0 = _inject_message_to_fd0_windows_safe


# ---------------------------------------------------------------------------
# Vendored-package resolution for the multi-file AnchoredWebReader contract
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
ANCHORED_CONTRACT_DIR = REPO_ROOT / "contracts" / "anchored_reader"
ANCHORED_CONTRACT_PATH = ANCHORED_CONTRACT_DIR / "anchored_reader.py"


def _evict_webanchor_modules() -> None:
    for key in [k for k in sys.modules if k == "webanchor" or k.startswith("webanchor.")]:
        del sys.modules[key]


@pytest.fixture
def deploy_anchored(direct_vm, direct_deploy):
    """Deploy ``AnchoredWebReader`` so its ``import webanchor`` resolves to the
    VENDORED copy at ``contracts/anchored_reader/webanchor/``, not the
    top-level ``webanchor`` package this repository also ships at its root.

    Why this is needed: ``gltest.direct.loader`` loads a contract file with
    ``importlib.util.spec_from_file_location`` and does not add the
    contract's own directory to ``sys.path`` (GenVM's real sandbox, by
    contrast, only ever sees the files inside a contract's own directory --
    there is no "repo root" for it to accidentally see). Running under plain
    ``pytest`` from the repository root, ``sys.path`` already contains the
    repo root, so a bare ``import webanchor`` inside the contract would
    silently resolve to the real top-level package instead of the vendored
    copy this test is supposed to be proving out. To make the test honestly
    exercise the vendored multi-file layout, this fixture:

    1. Evicts any cached ``webanchor``/``webanchor.*`` entries from
       ``sys.modules`` (e.g. left over from a test that imported the real
       package directly for a cross-check).
    2. Prepends the contract's own directory to the FRONT of ``sys.path``,
       so the vendored package is what Python's import machinery finds
       first.
    3. Deploys the contract (which executes its ``import webanchor`` at
       module-load time and binds that name, in the contract module's own
       globals, to whichever module object was resolved -- once bound, that
       reference is stable for the contract's lifetime regardless of later
       ``sys.path``/``sys.modules`` changes).
    4. Removes the inserted ``sys.path`` entry and evicts ``webanchor``
       modules again, so a subsequent direct ``import webanchor`` elsewhere
       in the same test session (e.g. the cross-check test) goes back to
       resolving the real top-level package.

    Returns a ``(deploy_fn, resolved_module)`` pair; ``resolved_module`` is
    the actual ``webanchor`` module object the contract bound, asserted by
    the caller (or by this fixture's own tests) to live under
    ``ANCHORED_CONTRACT_DIR`` and not under ``REPO_ROOT / "webanchor"``.
    """

    def _deploy(*args, **kwargs):
        _evict_webanchor_modules()
        sys.path.insert(0, str(ANCHORED_CONTRACT_DIR))
        try:
            contract = direct_deploy(str(ANCHORED_CONTRACT_PATH), *args, **kwargs)
        finally:
            try:
                sys.path.remove(str(ANCHORED_CONTRACT_DIR))
            except ValueError:
                pass
        resolved = sys.modules.get("webanchor")
        _evict_webanchor_modules()
        return contract, resolved

    return _deploy
