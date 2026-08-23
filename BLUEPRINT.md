# WebAnchor — Web-Evidence Normalization Layer for GenLayer

**Status:** v1 complete

---

## 1. The Problem

GenLayer's differentiator is intelligent contracts that read the web. But
consensus over web reads is structurally fragile:

- Leader and each validator issue **independent** HTTP requests.
- Between those requests: ads rotate, timestamps tick, A/B tests fire,
  session IDs are minted, CSRF nonces regenerate, view counters increment.
- Rate limits hit validator #3 with a 429 while the leader got a 200.
- Cloudflare/bot-wall pages return **HTTP 200** with challenge content.
- The page can simply update mid-consensus.

Today every contract author hand-rolls a defense. GenLayer's own docs
instruct developers to "extract stable fields" and "derive status from
variable data" — per contract, from scratch, every time. There is no
library. That is the gap WebAnchor fills.

## 2. The Thesis

> **WebAnchor turns a web read into a `strict_eq`-able fact.**

`gl.eq_principle.strict_eq` is the cheapest, strongest equivalence
principle — and it is unusable on raw web content because raw bytes never
match. WebAnchor sits between the fetch and the LLM, collapses every known
source of validator divergence, and emits a stable fingerprint that
`strict_eq` can actually compare. When it *cannot* produce a stable
result, it raises a **typed error** instead of letting a verdict be
derived from a 429 page.

## 3. API Surface (target)

```python
from webanchor import anchor, Policy
from webanchor.errors import RateLimited, BotWallDetected, UnstableContent

# The full path
ev = anchor("https://example.com/product/42", policy=Policy.default())

ev.fingerprint          # "wa1:<64 hex>"  — stable across validators
ev.text                 # normalized text, safe to feed an LLM
ev.bands                # {"price": "100.00-110.00"} canonicalized numbers
ev.meta                 # url, status, quantized fetch bucket, policy id
ev.to_calldata()        # dict of calldata-safe primitives

# The integration that makes it matter
def leader_fn():
    return anchor(url).to_calldata()

evidence = gl.eq_principle.strict_eq(leader_fn)   # <- now actually works
```

## 4. Hard Architectural Rules (non-negotiable)

**R1 — Zero third-party dependencies.** Stdlib only: `re`, `html.parser`,
`hashlib`, `json`, `unicodedata`, `datetime`, `dataclasses`, `typing`,
`enum`. No `bs4`, no `lxml`, no `requests`. GenVM runs a sandboxed
WASI Python; every extra dep is a deployment risk.

**R2 — The core must import and run with NO GenLayer present.**
Only `webanchor/fetch.py` may touch `genlayer` / `gl`, and it must import
lazily *inside the function body*, never at module top level. Everything
else — stripping, canonicalizing, fingerprinting — is pure functions over
strings. Consequence: the entire library is testable with plain `pytest`
on any machine, with no GenVM, no node, no network.

**R3 — Determinism is the product.** No `random`, no unseeded iteration
over sets, no `dict` ordering assumptions that aren't insertion-ordered,
no `time.time()` anywhere in the normalization path. Same input bytes
MUST produce the same fingerprint, on any machine, in any process, in any
Python 3.12+ build.

**R4 — Fail loudly, never silently.** Every failure path raises a typed
subclass of `WebAnchorError`. Returning empty string, `None`, or a
partial result on failure is a bug, not a fallback.

**R6 — Every constant that can change output must be versioned into
`policy_id`.** A module-level constant that alters behavior (control-char
thresholds, tag drop sets, depth caps) but is NOT folded into `policy_id`
causes the worst possible failure: two validators on different WebAnchor
versions silently disagree about the same bytes — one raises, one returns
text — with no visible signal. Behavioral constants belong either in
`Policy` or in a `BEHAVIOR_VERSION` that feeds the canonical JSON.

**R5 — Policy is explicit and versioned.** All tunable behavior lives in
a `Policy` object with a `policy_id` that feeds into the fingerprint.
Two validators on different policy versions must produce *visibly*
different fingerprints, not silently-diverging ones.

## 5. Module Layout

```
webanchor/
  __init__.py        public API re-exports: anchor, Policy, Evidence, errors
  errors.py          the typed error taxonomy
  policy.py          Policy dataclass + policy_id derivation
  text.py            unicode/whitespace canonicalization
  html_strip.py      volatile-DOM removal (html.parser based)
  numbers.py         number detection + banding
  timestamps.py      timestamp detection + quantization
  detect.py          429 / bot-wall / soft-error-page detection
  fingerprint.py     stable content hash
  evidence.py        Evidence dataclass + to_calldata()
  pipeline.py        normalize() — orchestrates the pure path
  fetch.py           gl.nondet.web wrappers + get_webpage compat shim
tests/
  fixtures/          captured real HTML, checked in
  ...
```

## 6. Milestones

| # | Name | Gate criteria |
|---|------|---------------|
| Foundation | Errors, Policy, fingerprint, Evidence | `pytest` green; core imports with no genlayer installed; determinism test passes across subprocesses |
| HTML stripper | Volatile-DOM removal | Strips script/style/noscript/iframe/svg/embed subtrees and comments; extracts TEXT, so attributes (nonce, CSRF, session ids) are eliminated structurally rather than by blocklist. Ad-container stripping is OPT-IN via `Policy.strict()` — conservative by default. Idempotent. |
| Canonicalizers | Text, numbers, timestamps | Number banding + timestamp quantization with documented, tested edge cases |
| Fetch + detection | `anchor()` | 429/bot-wall/soft-error detection raises typed errors; full end-to-end path |
| Benchmark | Divergence corpus + N-validator convergence proof | Simulated 5 validators over mutated real fixtures: raw diverges, WebAnchor converges |

Fast-follow (out of v1 scope, API designed to accommodate): demo
intelligent contract, benchmark report, published docs.

## 7. Definition of Done (every milestone)

1. Code written, no `TODO`/`pass`/`NotImplementedError` stubs in shipped paths.
2. Tests written **and actually executed**, output pasted in the report.
3. No third-party imports introduced.
4. Core still imports with genlayer absent.
5. Public names match this blueprint exactly, or the deviation is
   explicitly flagged and justified in the report.
