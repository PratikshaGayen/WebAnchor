# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""NaiveWebReader -- the strawman WebAnchor exists to replace.

This is what a developer writes today by following GenLayer's own web-access
docs literally, with no normalization: fetch the page, hand the raw bytes to
``gl.eq_principle.strict_eq``. It is correct GenVM code -- it compiles, it
deploys, it runs. The bug is not in this file; the bug is structural:
``strict_eq`` demands the leader and every validator produce byte-identical
output from *independent* HTTP requests, and real pages never hold still
between two fetches milliseconds apart -- rotating ads, minted CSRF nonces,
ticking timestamps, incrementing view counters. ``tests/direct/
test_naive_vs_anchored.py`` demonstrates the resulting divergence directly
against this contract, using two hand-verified "same page, two independent
fetches" fixtures.

See ``contracts/anchored_reader/anchored_reader.py`` for the fix: the same
shape, the same ``strict_eq``, with the fetch routed through
``webanchor.anchor`` instead of a raw ``gl.nondet.web.get``.
"""

from genlayer import *


class NaiveWebReader(gl.Contract):
    last_reading: str

    def __init__(self):
        self.last_reading = ""

    @gl.public.write
    def read_page(self, url: str) -> None:
        def fetch_raw_text():
            res = gl.nondet.web.get(url)
            return res.body.decode("utf-8", errors="replace")

        self.last_reading = gl.eq_principle.strict_eq(fetch_raw_text)

    @gl.public.view
    def get_last_reading(self) -> str:
        return self.last_reading
