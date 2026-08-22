# WebAnchor Demo

**Thesis:** WebAnchor turns a web read into a `strict_eq`-able fact — routing
a contract's fetch through `webanchor.anchor()` instead of a raw
`gl.nondet.web.get()` is the one-line change that turns two independent
validator fetches of the same page from a guaranteed consensus failure into
a guaranteed agreement.

## The two contracts

**`contracts/naive_reader.py`** is the strawman: exactly what GenLayer's own
web-access docs show today, with no normalization. It compiles, deploys, and
runs correctly — the bug is structural, not a mistake in this file.

```python
@gl.public.write
def read_page(self, url: str) -> None:
    def fetch_raw_text():
        res = gl.nondet.web.get(url)
        return res.body.decode("utf-8", errors="replace")
    self.last_reading = gl.eq_principle.strict_eq(fetch_raw_text)
```

**`contracts/anchored_reader/anchored_reader.py`** is the fix. Same shape,
same `strict_eq`, same public interface — only the fetch changes:

```python
@gl.public.write
def read_page(self, url: str) -> None:
    def fetch_anchored():
        evidence = webanchor.anchor(url, webanchor.Policy.default(), mode="get")
        return evidence.fingerprint
    self.last_fingerprint = gl.eq_principle.strict_eq(fetch_anchored)
```

`webanchor` is vendored as a sibling package inside `contracts/anchored_reader/`
(GenVM's multi-file runner only sees files inside a contract's own directory —
see the vendoring note in `contracts/anchored_reader/` for why).

## Run the proof

```
pytest tests/direct/ -v
```

No Docker, no Studio, no network — direct mode runs each contract in-process
against a mocked `gl.nondet.web.get`, in about 0.3s for all four tests.

## Results

Both contracts read the same "leader fetch" / "validator fetch" simulation:
`tests/fixtures/volatile_a.html` and `volatile_b.html`, two hand-verified
captures of the same order-status page that differ only in nonce, CSRF
token, timestamp, ad rotation, and build hash — exactly what changes between
two real, independent HTTP requests moments apart.

| Contract | Fetch A (leader) | Fetch B (validator) | Result |
|---|---|---|---|
| `NaiveWebReader` | `'<!DOCTYPE html>\n...spring-a...'` | `'<!DOCTYPE html>\n...spring-b...'` | **DIVERGES** — different strings, `strict_eq` fails |
| `AnchoredWebReader` | `wa1:eb9011c7b90b28cd...` | `wa1:eb9011c7b90b28cd...` | **CONVERGES** — identical fingerprint, `strict_eq` succeeds |

A fourth test confirms WebAnchor isn't just returning a constant: two
genuinely different pages (`news_article.html` vs `product_page.html`)
still produce different fingerprints (`wa1:65ae2e8e...` vs `wa1:b3ee1f84...`).

See `BENCHMARK.md` for the full measured convergence/divergence numbers
across a larger corpus.
