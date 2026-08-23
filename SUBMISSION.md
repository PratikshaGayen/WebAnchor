# WebAnchor — GenLayer Ecosystem Rewards Submission

**Category:** Projects
**Contribution type:** Builder

---

## Field-by-field draft for the submission form

### Contribution Type
`Builder`

### Category
`Projects` — not "Standalone Intelligent Contracts." WebAnchor is a library plus a demo
contract plus a measured benchmark: a complete workflow where GenLayer's consensus
primitives (`gl.nondet.web`, `gl.eq_principle.strict_eq`) are central, not a single
isolated contract.

### Description (paste into the "Describe your contribution..." field)

> **WebAnchor — a web-evidence normalization layer for GenLayer**
>
> GenLayer's differentiator is intelligent contracts that read the web. The problem:
> the leader and every validator fetch independently, and real pages never hold still
> between two fetches milliseconds apart — rotating ads, minted CSRF nonces, ticking
> timestamps, incrementing counters. GenLayer's own docs tell developers to hand-roll
> a defense per contract ("extract stable fields," "derive status from variable data").
> There was no library for this.
>
> **WebAnchor turns a web read into a `strict_eq`-able fact.** It sits between
> `gl.nondet.web` and the LLM: strips volatile DOM, canonicalizes numbers into
> versioned bands, redacts or quantizes timestamps, and emits a stable content
> fingerprint — or raises a typed error instead of silently producing a verdict from
> a bot-wall or soft-error page.
>
> Live demo (no install, no wallet): https://webanchor-demo.vercel.app
Two real transactions on GenLayer's hosted studionet against a page that
genuinely changes every request - one contract fails consensus, one doesn't.
>
> **What's included:**
> - A zero-dependency, pure-stdlib Python library (`webanchor/`, ~13 modules) that
>   imports and runs with no GenLayer SDK present — the entire normalization pipeline
>   is testable with plain `pytest`, no GenVM, no node, no network.
> - 1,335 automated tests, including a determinism suite that runs the pipeline across
>   three different `PYTHONHASHSEED` values in separate subprocesses to prove no output
>   is silently salted per-process, and a structural AST-based test that fails the build
>   if any file imports a non-stdlib module or imports GenLayer at module scope.
> - `BENCHMARK.md` — a mutation-based validator simulator run against a hand-written
>   corpus (news article, product page, dashboard, SSR-JSON page), measuring real
>   agreement rates across 25 simulated independent validator fetches, honestly
>   reporting the cases where convergence is *not* 100% and why.
> - A working demo: two GenVM contracts side by side — `NaiveWebReader` (reads the web
>   directly, exactly as GenLayer's own docs show, and fails consensus) and
>   `AnchoredWebReader` (identical shape, one call routed through WebAnchor, and
>   converges). Proven in under a second with no live network, using
>   `genlayer-test`'s direct-mode fixtures and two hand-verified "same page, two
>   independent fetches" HTML captures.
>
> **What we found along the way, not just what we built:** the benchmark surfaced a
> genuine gap in the local GenVM tooling — `genvm-lint` and `genlayer-test` both
> currently hardcode single-file (`py-genlayer`) contract validation and cannot
> validate a `py-genlayer-multi` contract's schema — plus a reproducible Windows bug
> in `genlayer-test` 0.29.2's direct-mode fd0 injection. Both are documented in the
> repo with reproductions, since they'll affect any multi-file GenVM contract, not
> just this one.
>
> Repo: https://github.com/PratikshaGayen/WebAnchor
> Key docs: `BENCHMARK.md` (measured numbers), `README_DEMO.md` (the 30-second proof),
> `BLUEPRINT.md` (architecture and the six non-negotiable rules the code is held to).

---

## Supporting links to attach

| What | Link | Why a reviewer opens it |
|---|---|---|
| Repository | https://github.com/PratikshaGayen/WebAnchor | The whole project |
| Demo proof | https://github.com/PratikshaGayen/WebAnchor/blob/main/README_DEMO.md | 30-second read: the two contracts, the exact command, the actual diverge/converge values |
| Benchmark | https://github.com/PratikshaGayen/WebAnchor/blob/main/BENCHMARK.md | The real, unrounded numbers — including the unflattering ones, explained |
| Architecture | https://github.com/PratikshaGayen/WebAnchor/blob/main/BLUEPRINT.md | The six rules (R1–R6) the whole codebase is structurally tested against |
| Library | https://github.com/PratikshaGayen/WebAnchor/tree/main/webanchor | ~9,500 lines across the package + tests |
| Demo contracts | https://github.com/PratikshaGayen/WebAnchor/tree/main/contracts | The side-by-side proof artifact |

---

## Status

Pushed and verified live at https://github.com/PratikshaGayen/WebAnchor — public, one
clean commit, author attribution confirmed on GitHub's own commit page.

The description above is a draft assuming the "Projects" category's actual form
fields match the screenshot (Contribution Type, category picker, free-text
description). If the real form asks for structured fields (tech stack, links,
team members) that aren't a single textarea, say so and it can be reshaped to match.
