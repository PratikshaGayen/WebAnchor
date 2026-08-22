# WebAnchor Benchmark

**Thesis:** WebAnchor turns a web read into a `strict_eq`-able fact — it sits
between an HTTP fetch and an LLM, collapses the known sources of validator
divergence (nonces, CSRF tokens, ad rotation, whitespace reflow, absolute
timestamps), and raises a typed error instead of letting a bot-wall or
soft-error page produce a consensus verdict.

This document reports what a mutation-based validator simulator actually
measured against that thesis, on a hand-written corpus shaped like real
pages. Every number below is copied verbatim from one run of
`python tools/corpus_bench.py`; none are hand-typed or rounded up.

## Headline table

25 simulated independent validator fetches per fixture, all five mutators
composed (`nonce_and_csrf` + `timestamps` + `ad_slot` + `counter` +
`whitespace`), reporting **agreement rate** = (fingerprints matching the
modal/majority fingerprint) / 25 — not pairwise agreement, but the number
`strict_eq` actually cares about: does a validator's independent computation
match the value the leader proposed.

| fixture | raw agreement | Policy.default() agreement | Policy.strict() agreement |
|---|---|---|---|
| news_article.html | 1/25 | 1/25 | 1/25 |
| product_page.html | 1/25 | 1/25 | 1/25 |
| api_style_json_in_html.html | 1/25 | 1/25 | 11/25 |
| dashboard_stats.html | 1/25 | 1/25 | 1/25 |

**Read this table carefully — it is not the flattering headline it might look
like at a glance, and the section immediately below explains why.** The raw
column is 1/25 everywhere by construction: it is the strawman, and any of the
five mutators alone is enough to make every one of 25 raw-byte captures
distinct. The `default`/`strict` columns are the real result, and they are
**not** uniformly good. See "Why the composed numbers are low" immediately
below before drawing a conclusion from this table alone.

## Why the composed numbers are low: a per-mutator breakdown

The composed table above hides *which* mutation class is responsible for
divergence. Isolating each mutator (applied alone, not composed) answers
that:

| fixture | mutator | raw | default | strict |
|---|---|---|---|---|
| news_article.html | nonce_and_csrf | 1/25 | 25/25 | 25/25 |
| news_article.html | timestamps | 1/25 | 25/25 | 25/25 |
| news_article.html | ad_slot | 1/25 | 25/25 | 25/25 |
| news_article.html | counter | 1/25 | 1/25 | 1/25 |
| news_article.html | whitespace | 1/25 | 13/25 | 13/25 |
| product_page.html | nonce_and_csrf | 1/25 | 25/25 | 25/25 |
| product_page.html | timestamps | 1/25 | 25/25 | 25/25 |
| product_page.html | ad_slot | 1/25 | 25/25 | 25/25 |
| product_page.html | counter | 1/25 | 1/25 | 1/25 |
| product_page.html | whitespace | 1/25 | 25/25 | 25/25 |
| api_style_json_in_html.html | nonce_and_csrf | 1/25 | 25/25 | 25/25 |
| api_style_json_in_html.html | timestamps | 1/25 | 25/25 | 25/25 |
| api_style_json_in_html.html | ad_slot | 1/25 | 25/25 | 25/25 |
| api_style_json_in_html.html | counter | 1/25 | 1/25 | 11/25 |
| api_style_json_in_html.html | whitespace | 1/25 | 25/25 | 25/25 |
| dashboard_stats.html | nonce_and_csrf | 1/25 | 25/25 | 25/25 |
| dashboard_stats.html | timestamps | 1/25 | 25/25 | 25/25 |
| dashboard_stats.html | ad_slot | 1/25 | 25/25 | 25/25 |
| dashboard_stats.html | counter | 1/25 | 1/25 | 1/25 |
| dashboard_stats.html | whitespace | 1/25 | 25/25 | 25/25 |

**The finding:** three of the five mutation classes — nonce/CSRF rewriting,
ad-slot/creative-id rotation, and (in three of four fixtures) whitespace
reflow — converge **perfectly, 25/25, under both policies**. WebAnchor
structurally eliminates attribute-borne volatility (nothing an attribute
carries ever reaches extracted text) and drops ad payloads that live inside
`<iframe>`/`<script>` subtrees regardless of policy. That part of the thesis
holds without qualification on this corpus.

**The composed table is dragged down entirely by one mutation class:
`counter`.** Two honest, distinct reasons, not one:

1. **`Policy.default()` never converges on a mutated counter, by design.**
   `number_band_mode` defaults to `"none"` — `Policy.default()` does not band
   numbers at all, so "58 people are viewing this" and "62 people are
   viewing this" are different strings, full stop. This is not a bug; it is
   the documented, conservative default (`policy.py`: *"Nothing that could
   be prose is discarded... ad-container heuristics and number banding stay
   off"*). A contract anchoring a page with a live counter under
   `Policy.default()` gets zero protection against that counter, and this
   benchmark is the first place that is stated as a measured number (1/25)
   rather than as a caveat in a docstring.

2. **`Policy.strict()` (`number_band_mode="significant"`) converges only
   when a page has a single mutated counter, and even then only
   partially.** `api_style_json_in_html.html` has exactly one counter
   ("2,291 people are tracking...") and reaches 11/25 under `strict` —
   `behavior.py`'s documented residual-divergence property in action: a
   3-significant-digit rounding bucket is roughly 10 units wide at this
   magnitude, and the 25 simulated readings (2291..2315) straddle two
   buckets, so a majority — but not all — land in one. The other three
   fixtures each have **two independently-mutated counters** (e.g.
   `dashboard_stats.html`: "1,507 people viewing" and "311 online now"), and
   land at 1/25 — exactly the compounding effect `behavior.py` predicts
   ("`n` independent bucketed values... multiply the per-value survival
   probability"). Two counters whose buckets each flip on roughly half the
   range essentially never agree across all 25 draws simultaneously.

**Every cell in the composed table that is not `25/25` is explained by the
`counter` mutator, and none of it is a surprise once `behavior.py` is read —
it is that document's central claim, measured rather than argued.**

## Cells not at 100% (explicit)

Explicitly, per the requirement not to assume 100% anywhere:

- `Policy.default()` is **1/25** — not 100% — on every fixture with a
  mutated counter (all four), because default policy does not band numbers.
- `Policy.strict()` is **11/25** on `api_style_json_in_html.html` (single
  counter, partial bucket agreement) and **1/25** on the other three
  (multiple counters, compounding divergence).
- The `whitespace` mutator alone is **13/25**, not 25/25, on
  `news_article.html` under *both* policies. This is a genuine, narrow
  finding, not a mutator artifact — see the next section.

## An unplanned finding: whitespace reflow is not always invisible

The blueprint's claim (and the mutator's job) is that reflowing indentation
between tags cannot change extracted text, because `collapse_layout`
collapses horizontal whitespace and strips every line. That claim holds for
whitespace between **block-level** (`BOUNDARY_TAGS`) elements on every
fixture tested. It does **not** fully hold for `news_article.html`, where the
mutator reproducibly splits into two output classes (13/25 vs 12/25).

The cause: `news_article.html`'s nav bar has `<a>Transit</a>` immediately
followed by whitespace and then `<span class="session-badge">`. Neither
`<a>` nor `<span>` is in `BOUNDARY_TAGS`, so the HTML parser does not emit a
structural boundary there itself — the *only* newlines between those two
inline elements are whatever raw whitespace sits in the source between the
tags, which is exactly what `mutate_whitespace` varies. `collapse_layout`
squeezes 3+ newlines down to 2 but does **not** collapse the 1-vs-2
distinction below that, because that distinction is a paragraph/no-paragraph
signal it is supposed to preserve elsewhere in the document. Whitespace
reflow that happens to change a 1-vs-2 newline count *between two inline
elements* therefore flips whether a blank line appears in the normalized
text — a real, if narrow, residual divergence source that is distinct from
(and not addressed by) any of R1-R6, and is not something this benchmark
was designed to go looking for; it fell out of running it.

Practically: this requires whitespace reflow to land exactly on a
1-vs-2-newline count *between two adjacent inline elements with no
intervening text*, which is a narrower and rarer real-world condition than
generic indentation reflow. It is reported here rather than smoothed over
because the benchmark exists to find exactly this kind of thing.

## Cloudflare-branch finding (measured, then fixed)

It was left open earlier whether the `Server:` + shape-marker branch in
`webanchor.detect._check_bot_wall` (fires only when `Server:` names a
challenge-capable edge *and* the visible body matches a `CHALLENGE_SHAPE_MARKERS`
phrase) is worth the false-positive risk. This section originally reported
the measurement; it now also reports the fix that measurement produced.

**Original measurement, before the fix (unconditional branch):**
`tests/fixtures/corpus/small_legit/` — 5 tiny, realistic non-challenge pages
(a landing stub, a redirect stub, a status endpoint, a "coming soon" page, a
minimal FAQ) — run through `check_response` with `Server: cloudflare`.
**2/5** false positives: `coming_soon.html` (fires on `"please wait"`) and
`redirect_stub.html` (fires on `"redirecting"`). On the actual
`tests/fixtures/cloudflare_challenge.html` fixture, the **strong-marker
tier alone** already fires (`cf-browser-verification`) — the branch was
never the only signal on the fixture it was built for. Measured: a 40%
false-positive rate for a 0% measured marginal benefit.

**The fix:** the branch is now gated behind a new `Policy` field,
`detect_bot_wall_server_hint: bool = False`. `Policy.default()` leaves it
off; `Policy.strict()` turns it on. Strong- and weak-marker detection are
unaffected and remain unconditional — the data showed those two tiers alone
already catch the real fixture. Adding the field changed `policy_id` for
both built-in policies (a `Policy` field is exactly how R5/R6 want a
behavior-affecting toggle exposed: two validators disagreeing about this
setting now disagree about `policy_id` itself, visibly, rather than about
a hidden module constant): the new locked ids are
`Policy.default().policy_id == "p1:93ae77e034942b7acf8ac9bf3e4a718c"` and
`Policy.strict().policy_id == "p1:9f2d5ca046dda774bc68d4c2e22cca9a"`.

**Re-measured after the fix**, same small-legit corpus, same
`Server: cloudflare` header, both built-in policies:

- **`Policy.default()` (branch OFF): 0/5 false positives.** The exact two
  pages that previously false-positived (`coming_soon.html`,
  `redirect_stub.html`) now raise nothing under the policy every caller gets
  by default.
- **`Policy.strict()` (branch ON, opt-in): 2/5 false positives**, unchanged
  from the original measurement — this is expected and correct: `strict()`
  is the "you know your page and accept aggressive-heuristic trade-offs"
  policy, and this branch is exactly that kind of trade-off. A caller who
  explicitly opts into `strict()` is choosing to accept this risk.
- On `tests/fixtures/cloudflare_challenge.html`, unchanged: the
  strong-marker tier alone still fires regardless of the flag, confirming
  the branch is genuinely redundant on the one real bot-wall fixture in this
  repository, under both policies.

**Verdict:** the default path no longer pays a measured 40% false-positive
cost for a measured 0% marginal benefit; a caller who wants the extra
heuristic anyway (larger unknown corpus, different risk tolerance) opts in
explicitly via `Policy.strict()` and gets exactly the previously-measured
behavior, unchanged. Five fixtures remains a small sample — this is a
strong signal from a small corpus, not a claim that a larger corpus would
show the same 40%, and the flag exists precisely so that tightening or
loosening this trade-off never again requires a silent behavior change.

## Ad-stripping content-loss (resolved with data)

`Policy.strict()` turns on `strip_ad_containers`, which drops whole subtrees
whose class/id matches a generic pattern (`ad`, `banner`, `sponsor`, `promo`,
...). The concern: generic names collide with real editorial
content. Measured by normalizing each fixture twice under otherwise-identical
`Policy.strict()` settings, differing only in `strip_ad_containers`
(isolating that one variable):

- `product_page.html`: **145 of 967 characters removed (14.99%)** — the
  `<div class="promo">` block ("Members save an extra 10%...") is real
  promotional-but-editorial copy, not an ad, and it is deleted outright.
- `news_article.html`: **181 of 1494 characters removed (12.11%)** — a
  `<div class="sponsored">` community-spotlight paragraph, again real prose,
  deleted for the same reason.

**Note on measurement design:** the `<iframe>`-wrapped ad creative present
in every fixture contributes **zero** to this number, because `<iframe>` is
unconditionally dropped by `html_strip.DROP_SUBTREE_TAGS` regardless of
`strip_ad_containers` — ad-container stripping's only *measurable* effect in
this corpus is on the directly-visible, non-iframe blocks tagged with a
generic ad-adjacent class name. That is precisely the risk `policy.py`'s own
docstring names ("a real ad box lives at `class='sponsor'`... a real
editorial module about sponsors also lives at `class='sponsor'`"), now with
a number attached: on this corpus, roughly 12-15% of the surrounding
section's content is silently lost per occurrence.

## Timestamp boundary-straddle sweep

**Caveat first:** neither `Policy.default()` nor `Policy.strict()` actually
uses `timestamp_mode="quantize"` — both leave `timestamp_mode` at its
Policy-wide default, `"redact"` (`Policy.strict()` overrides
`number_band_mode`, `timestamp_quantum_seconds`, `lowercase`, and
`strip_ad_containers`, but not `timestamp_mode`). This is worth stating
explicitly: the design doc's description implies `strict()` is the
"quantize timestamps" policy, but as shipped it is not — `strict()` bands
numbers aggressively and redacts timestamps just like `default()` does. The
sweep below therefore uses an explicit `Policy(timestamp_mode="quantize",
timestamp_quantum_seconds=3600)`, not either built-in constructor, because
neither built-in policy exercises `quantize` at all.

**Setup:** `mutate_timestamps` shifts every ISO-8601 timestamp in
`dashboard_stats.html` by `i` seconds, `i` swept from 0 to `2 *
timestamp_quantum_seconds` = 7200, against an hourly (3600s) quantum.
Normalized text at each shift is compared to the `i=0` baseline.

**Result:** divergence from baseline observed at **4326/7201 sampled shifts
(60.07%)**, with the **first divergence at shift=2875s**.

**Does it match the documented formula?** `behavior.py` states
`residual divergence risk ~= spread / quantum`, valid "while `spread <<
bucket_width`" and explicitly *not* a guarantee once spread approaches the
bucket width. At `shift = quantum` (spread equals the bucket width exactly),
the formula's own stated approximation has already broken down — this is
outside the regime it claims to describe, and the measured behavior confirms
that rather than contradicting the formula: first divergence at 2875s against
a 3600s quantum is `2875/3600 ≈ 0.7986` of the way to the quantum, i.e.
divergence starts well before the naive spread/quantum=1.0 point, consistent
with `dashboard_stats.html` containing **more than one** absolute timestamp
(the "last updated" line and the `data-timestamp` attribute value both carry
`2024-09-14T00:12:05Z`) — each additional timestamp is another chance to
straddle its own bucket edge first, exactly as `behavior.py` warns
("compounds across values"). The formula's qualitative shape (divergence
risk rising monotonically toward the quantum, never reaching zero, and
"the approximation holds while spread << bucket_width" ceasing to hold well
before spread == quantum) matches; the specific fixture's *first-divergence
point* is earlier than a naive single-timestamp reading of the formula would
suggest, precisely because there is more than one timestamp on the page.

## How to reproduce

```
python tools/corpus_bench.py
```

Deterministic and requires no network, no GenLayer runtime, and no random
seed — every mutator derives its variation from `sha256(label:i)`. Verified
by running it twice and diffing:

```
python tools/corpus_bench.py > run_a.txt
python tools/corpus_bench.py > run_b.txt
diff run_a.txt run_b.txt   # empty; sha256sum run_a.txt run_b.txt matches
```

Both runs produced the identical SHA-256
`5ffa151bbff0a2fe918bc3334a20194199990844738729675c06b5db61e1d4b4`.

Unit tests for the mutators and the simulator's own determinism:

```
python -m pytest tests/test_corpus_bench.py -v
```

## Limitations (what WebAnchor does not solve)

- **Un-banded counters under `Policy.default()`.** Stated above with a
  number, not a caveat: 1/25 agreement on every fixture with a live counter.
  A contract that needs a counter-bearing page to converge must opt into
  `number_band_mode` explicitly and size the bucket against its own expected
  read spread; there is no default that protects this case, by design.
- **Multiple independent bucketed values compound.** Even with banding on,
  a page with two or more volatile numbers converges markedly worse than one
  with a single volatile number, because each is an independent chance to
  straddle a bucket edge. This is measured above (11/25 vs 1/25 on
  structurally similar pages) and is not something a larger bucket alone
  fixes — the formula's own compounding term does not go away.
- **No bucketing scheme reaches zero residual risk.** Only redaction
  (`timestamp_mode="redact"`, the actual default for both `Policy.default()`
  and `Policy.strict()`) has a total guarantee. Quantization and number
  banding are risk-reduction, not elimination, and this benchmark's sweep
  measured that directly rather than taking it on faith.
- **Divergence from genuinely different content is out of scope.** If the
  leader and a validator legitimately fetch different content — a page that
  changed between requests in a way that is not one of the volatility
  classes modeled here, an A/B test bucket that changes the actual
  substance of the page, or a CDN edge serving different real content by
  region — WebAnchor has no way to distinguish that from server-side
  content drift, and neither does this benchmark; it only measures the
  known, modeled divergence classes.
- **JS-rendered content is untested here.** `webanchor.fetch` supports a
  `render` mode for pages that need JavaScript execution to produce their
  final DOM, but this corpus is static HTML: every fixture in this
  benchmark is what `mode="html"`/`"get"` sees, not what a renderer produces
  after JavaScript execution. The render-mode blind spot documented in
  `pipeline.anchor` (no HTTP status available) is not exercised by anything
  in this document.
- **Adversarial content manipulation upstream of the fetch is out of
  scope.** `fingerprint.verify`'s own docstring states this plainly: a
  matching fingerprint proves the same normalized text was hashed, not that
  the underlying content is authentic or untampered. A malicious origin
  server (or a compromised CDN edge) that serves genuinely fabricated
  content to every validator identically produces perfect `strict_eq`
  agreement on a wrong fact. WebAnchor solves *validator-side* divergence;
  it is not a defense against a dishonest source.
- **The Server:+shape bot-wall branch is now opt-in, not proven safe at any
  setting.** The branch is gated behind `Policy.detect_bot_wall_server_hint`
  (off by `Policy.default()`, on by `Policy.strict()`) as a direct result of
  this benchmark's Cloudflare-branch measurement — a 40% false-positive rate
  on a 5-fixture corpus with zero marginal benefit on the one real bot-wall
  fixture in this repository. Gating removes the cost from the default
  path; it does not certify the branch as safe under `Policy.strict()` for
  a caller who opts in — 5 fixtures is not a large sample, and a larger
  corpus could still move that 2/5 in either direction.
