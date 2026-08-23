"""Fixtures for Studio-mode (live network) integration tests.

These tests run against a REAL GenLayer Studio stack (three consensus-worker
containers doing real leader+validator consensus over JSON-RPC), not the
in-process direct-mode simulator used by ``tests/direct/``. See
``tests/integration/volatile_server.py`` for why a real HTTP server is used
instead of ``gltest``'s ``mock_web_response`` (which returns identical bytes
to every caller and therefore cannot demonstrate real per-request
volatility).

Networking note: the GenLayer Studio consensus-worker containers run in
Docker Desktop's VM and cannot reach a server bound to ``127.0.0.1`` on the
Windows host. ``host.docker.internal`` is the working route (confirmed by
``docker exec <worker> curl http://host.docker.internal/...`` from inside a
running worker container). The web module's Lua URL-checker additionally:

  * only allows ports 80 and 443 (``lib-web.lua``: ``PORT_FORBIDDEN`` for any
    other port), so the fixture server MUST bind to port 80, and
  * only allows a request host whose TLD is on `allowed_tld` or whose full
    host string is on `always_allow_hosts` (``TLD_FORBIDDEN`` otherwise) --
    ``.internal`` is not a recognized public-suffix TLD, so
    ``host.docker.internal`` must be added to `always_allow_hosts`.

Both of those were confirmed the hard way (real ``PORT_FORBIDDEN`` and
``TLD_FORBIDDEN`` errors from real transactions) before this fixture was
written -- see the integration report for the exact commands and output.
`always_allow_hosts` was patched into each running consensus-worker's
``/genvm/config/genvm-module-web.yaml`` via `docker cp`, then the worker's
Web module (not the container) was restarted through its own manager HTTP
API (`POST /module/stop` + `POST /module/start` on port 3999 inside each
container) to pick up the change. No container was stopped or restarted.
"""

import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from volatile_server import VolatileServer  # noqa: E402

VOLATILE_PORT = 80
# GenVM's web module only allows ports 80/443, so the URL below intentionally
# has NO port suffix (defaults to 80 for http://) and uses the Docker-Desktop
# host-reachable hostname rather than 127.0.0.1 / localhost.
VOLATILE_HOST = "host.docker.internal"
VOLATILE_URL = f"http://{VOLATILE_HOST}/orders/100418"


def _can_bind_port_80() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("0.0.0.0", VOLATILE_PORT))
        return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def volatile_server():
    """A real HTTP server, reachable from the Studio containers, that returns
    genuinely different bytes on every single request (fresh nonce, CSRF
    token, timestamp, ad id, viewer counter -- see volatile_server.py)."""
    if not _can_bind_port_80():
        pytest.skip(
            "Cannot bind port 80 on this host (GenVM's web module only "
            "allows ports 80/443, so the fixture server must use port 80). "
            "Run this test suite with permission to bind privileged ports."
        )
    server = VolatileServer(host="0.0.0.0", port=VOLATILE_PORT).start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture(scope="session")
def volatile_url(volatile_server) -> str:
    return VOLATILE_URL
