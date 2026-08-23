"""A tiny HTTP server that serves genuinely volatile content on every request.

Used by the Studio-mode integration tests (``tests/integration/``) to prove
real network divergence: each request gets a fresh random nonce, a
fresh-to-the-microsecond timestamp, and a fresh "ad id", embedded in an
otherwise-static HTML page shaped like ``tests/fixtures/volatile_a.html`` /
``volatile_b.html``. No two requests -- from the leader or from any
validator -- ever produce byte-identical output, because the values are
generated freshly inside the request handler, not read from a fixed pool of
alternatives.

This is deliberately NOT ``gltest``'s ``mock_web_response``, which returns
identical bytes to every caller for a given URL pattern -- that mechanism
cannot demonstrate real per-request volatility because there is only one
"response" object to match against. This module launches a real
``http.server`` that a real GenVM web-fetch (leader or validator, in-process
or in a Docker container) hits over a real socket, gets genuinely different
bytes back each time, and that is the honest reproduction of "the leader and
each validator make independent HTTP requests to the same URL and see
different content."

Usage (used by ``tests/integration/conftest.py`` as a fixture, but also
runnable standalone for manual poking):

    python tests/integration/volatile_server.py [port]
"""

from __future__ import annotations

import secrets
import string
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Shaped after tests/fixtures/volatile_a.html / volatile_b.html (the
# hand-verified "same page, two independent fetches" pair WebAnchor's own
# direct-mode proof uses). The volatility classes WebAnchor's default policy
# is proven to collapse -- nonces/CSRF tokens, build/request ids, ad
# rotation, timestamps -- all live inside <script> bodies, HTML comments,
# element ATTRIBUTES, or an <iframe> subtree, exactly like the fixtures.
# Every VISIBLE text node is static prose, identical across requests.
# This is deliberate, not an oversight: WebAnchor's normalization extracts
# TEXT and structurally drops <script>/<iframe> subtrees and comments, but it
# does NOT band arbitrary numbers or redact arbitrary random strings that
# happen to sit in ordinary visible prose (Policy.default() leaves
# number_band_mode="none" -- see BENCHMARK.md's "Un-banded counters" finding).
# A volatile *viewer counter* or *session id* rendered directly into visible
# text is a real, previously-measured non-convergence case, not something
# this fixture is trying to demonstrate; putting freshness only where
# WebAnchor already structurally handles it is what makes this an honest
# reproduction of "the leader and each validator fetch different bytes and
# still agree," rather than a case the library was never designed to solve.
PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Order Status</title>
  <script nonce="{nonce}">
    window.__RENDERED_AT__ = "{iso_timestamp}";
    window.__REQUEST_ID__ = "req-{request_id}";
  </script>
</head>
<body>
  <!-- build: {build_hash} deployed {iso_timestamp} from host web-{host_id} -->
  <header><h1>Order Status</h1></header>
  <main>
    <p>Order 100418 was shipped on 12 April 2024.</p>
    <p>Carrier: Northwind Freight. Tracking: NW-88213-XZ.</p>
    <table>
      <tr><th>Item</th><th>Qty</th></tr>
      <tr><td>Widget 42</td><td>3</td></tr>
      <tr><td>Bracket 7</td><td>1</td></tr>
    </table>
    <form action="/orders/100418/cancel" method="post">
      <input type="hidden" name="csrf_token" value="{csrf}">
      <button type="submit">Cancel order</button>
    </form>
    <div class="ad-slot" data-campaign="{campaign}" data-nonce="{nonce}">
      <iframe src="https://ads.example.com/creative/{ad_id}?cb={cache_bust}" title="advert">
        Rotating creative: {ad_copy}
      </iframe>
      <script nonce="{nonce}">renderAd("{ad_id}", {cache_bust});</script>
    </div>
    <p>Delivery is expected within two business days.</p>
  </main>
  <footer><p>Northwind Trading Company</p></footer>
</body>
</html>
"""

_ALPHABET = string.ascii_lowercase + string.digits
_AD_COPIES = (
    "40% off garden tools, today only!",
    "free shipping on orders over 50 euro!",
    "buy one get one on all winter coats!",
    "flash sale: 25% off checkout in the next hour!",
)


def _rand(n: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


class VolatileHandler(BaseHTTPRequestHandler):
    # Quiet the default per-request stderr logging; the test output is noisy
    # enough already with three validators' worth of real HTTP traffic.
    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass

    def do_GET(self):  # noqa: N802 - stdlib method name
        now = time.time()
        body = PAGE_TEMPLATE.format(
            nonce=_rand(24),
            iso_timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            request_id=_rand(8),
            build_hash=_rand(10),
            host_id=secrets.randbelow(99) + 1,
            csrf=_rand(32),
            campaign=_rand(8),
            ad_id=_rand(8),
            cache_bust=int(now * 1000),
            ad_copy=secrets.choice(_AD_COPIES),
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class VolatileServer:
    """Context-manager wrapper around a background ThreadingHTTPServer."""

    def __init__(self, host: str = "0.0.0.0", port: int = 0):
        self._httpd = ThreadingHTTPServer((host, port), VolatileHandler)
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def start(self) -> "VolatileServer":
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = VolatileServer(port=port).start()
    print(f"volatile server listening on 0.0.0.0:{server.port}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop()
