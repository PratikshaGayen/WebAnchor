# WebAnchor

**A web-evidence normalization layer for GenLayer intelligent contracts.**

GenLayer's differentiator is contracts that read the web. The problem: the leader
and every validator fetch independently, and real pages never hold still between
two fetches milliseconds apart — rotating ads, minted CSRF nonces, ticking
timestamps, incrementing counters. GenLayer's own docs tell developers to
hand-roll a defense per contract ("extract stable fields," "derive status from
variable data"). There was no library for this.

**WebAnchor turns a web read into a `strict_eq`-able fact.** It sits between
`gl.nondet.web` and the LLM: strips volatile DOM, canonicalizes numbers into
versioned bands, redacts or quantizes timestamps, and emits a stable content
fingerprint — or raises a typed error instead of silently producing a verdict
from a bot-wall or soft-error page.

```python
import webanchor

evidence = webanchor.anchor("https://example.com/product/42")
evidence.fingerprint   # "wa1:<64 hex>" — stable across independent validator fetches
evidence.text          # normalized text, safe to feed an LLM
```

## What's here

| Path | What it is |
|---|---|
| [`webanchor/`](webanchor/) | The library. Zero third-party dependencies — stdlib only. Imports and runs with no GenLayer SDK present. |
| [`contracts/`](contracts/) | Two GenVM contracts side by side: `naive_reader.py` (reads the web directly, fails consensus) and `anchored_reader/` (same shape, routed through WebAnchor, converges). |
| [`tests/`](tests/) | 1,335 tests for the library, 4 direct-mode tests proving the two contracts diverge/converge as claimed. |
| [`tools/corpus_bench.py`](tools/corpus_bench.py) | The mutation-based validator simulator behind `BENCHMARK.md`. |
| [`BLUEPRINT.md`](BLUEPRINT.md) | Architecture and the six non-negotiable rules (R1–R6) the codebase is structurally tested against. |
| [`BENCHMARK.md`](BENCHMARK.md) | Measured agreement rates across a hand-written page corpus — including the cases that are *not* 100%, and why. |
| [`README_DEMO.md`](README_DEMO.md) | The 30-second proof: run one command, see a naive read fail and an anchored read converge. |
| [`SUBMISSION.md`](SUBMISSION.md) | GenLayer ecosystem rewards submission draft. |

## Quickstart

```bash
git clone https://github.com/PratikshaGayen/WebAnchor.git
cd WebAnchor
pip install -e ".[dev]"

python -m pytest tests/ -v         # the library, 1335 tests, no network, no GenVM
python -m pytest tests/direct/ -v  # the demo contracts, 4 tests, no live node
python tools/corpus_bench.py       # the benchmark numbers in BENCHMARK.md
```

## Why this is real, not a mock

- **Structurally enforced, not just documented.** An AST-based test walks every file
  in `webanchor/` and fails the build if anything imports a non-stdlib module, or
  imports GenLayer at module scope outside `fetch.py`. The rule can't quietly rot.
- **Determinism is proven across processes.** A test suite runs the pipeline under
  three different `PYTHONHASHSEED` values in separate subprocesses and demands
  byte-identical output — catching the class of bug where a fingerprint would
  silently differ per validator for reasons that have nothing to do with the page.
- **The benchmark reports its own limits.** `BENCHMARK.md` includes a case where
  agreement is 1 out of 25 simulated validators and explains exactly why, instead
  of only showing numbers that make the library look good.
- **The demo proves both directions.** The anchored contract doesn't just converge
  on the same page fetched twice — a separate test confirms it still produces
  different fingerprints on two genuinely different pages, so it isn't a constant
  function wearing a costume.

## Status

v1 complete. Six build phases (foundation → HTML stripping → canonicalization →
fetch/detection → benchmark → demo contract), each independently verified. See
`BLUEPRINT.md` for the full scope and `BENCHMARK.md` for the numbers.
