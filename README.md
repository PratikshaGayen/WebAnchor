# WebAnchor

A normalization layer that sits between a GenLayer contract's web fetch and the LLM.

Here's the problem it's solving. On GenLayer, the leader and every validator hit a
URL independently. Real pages don't hold still between those requests - ads
rotate, CSRF tokens get reissued, timestamps tick over, view counters go up by
one. So `gl.eq_principle.strict_eq`, which is the cheap "just compare and agree"
primitive, basically never works on raw web content. Everyone ends up hand-rolling
their own fix per contract (GenLayer's own docs even tell you to go extract
stable fields yourself). There wasn't a library for this, so I built one.

WebAnchor strips the volatile stuff out of a page - nonces, timestamps, comment
blocks, ad slots - canonicalizes what's left (numbers get banded, timestamps get
redacted or bucketed depending on policy), and hashes the result into a
fingerprint. Two validators hitting the same page a second apart should get the
same fingerprint even though they pulled different bytes. If it can't produce
something stable, it throws a typed error instead of quietly handing garbage to
the LLM.

```python
import webanchor

evidence = webanchor.anchor("https://example.com/product/42")
evidence.fingerprint   # "wa1:..." - stable across independent fetches
evidence.text          # normalized text, safe to hand to an LLM
```

## Layout

- `webanchor/` - the library itself. No dependencies, stdlib only, and it imports
  fine even without the GenLayer SDK installed.
- `contracts/` - two demo contracts. `naive_reader.py` reads the web the obvious
  way and fails consensus. `anchored_reader/` does the same thing routed through
  WebAnchor and doesn't.
- `tests/` - 1,335 tests for the library, plus a handful of direct-mode tests
  that actually deploy both contracts and show one failing, one passing.
- `tools/corpus_bench.py` - the script behind the numbers in BENCHMARK.md.
- `BLUEPRINT.md` - architecture notes and the rules I held the code to.
- `BENCHMARK.md` - measured agreement rates, including the runs that didn't hit
  100% and why.
- `README_DEMO.md` - the short version of the proof, one command.
- `SUBMISSION.md` - draft text for the GenLayer ecosystem rewards submission.

## Running it

```bash
git clone https://github.com/PratikshaGayen/WebAnchor.git
cd WebAnchor
pip install -e ".[dev]"

pytest tests/ -v              # library tests, no network, no GenVM needed
pytest tests/direct/ -v       # the two demo contracts, still no live node
python tools/corpus_bench.py  # regenerates the benchmark numbers
```

## A few things worth knowing before you trust it

There's a test that walks the AST of every file in `webanchor/` and fails the
build if something imports a non-stdlib package, or imports GenLayer outside of
`fetch.py`. I wanted that rule actually enforced, not just written down
somewhere and forgotten about three weeks later.

Determinism gets checked across process boundaries too - the same input run
under three different `PYTHONHASHSEED` values has to come out byte-identical.
It's the kind of bug that's easy to introduce by accident and only shows up
months later as validators mysteriously disagreeing on nothing.

BENCHMARK.md isn't all good news, on purpose. One test case only converges 1
out of 25 times, and I wrote up exactly why instead of quietly leaving it out.
The anchored contract also gets checked against two genuinely different pages
to make sure it still disagrees on those - otherwise it'd just be a constant
pretending to be useful.

## Where it's at

v1, and I'd call it done: library, HTML stripping, text/number/timestamp
canonicalization, fetch + bot-wall detection, the benchmark, then the demo
contracts, each one checked before I moved on to the next. `BLUEPRINT.md` has
the full breakdown, `BENCHMARK.md` has the numbers.
