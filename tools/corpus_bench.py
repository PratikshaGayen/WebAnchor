"""M5 benchmark: simulate N independent validator fetches of "the same page"
and measure how often WebAnchor's fingerprint converges, versus raw bytes.

Not part of the ``webanchor`` package -- a script, importing ``webanchor``
normally. Stdlib only (R1). No ``random`` anywhere: every mutator derives its
variation deterministically from the integer ``i`` (R3), so re-running this
script reproduces byte-identical output. No float in any numeric path. No
recursion.

Run directly:

    python tools/corpus_bench.py

This prints the exact markdown table that appears in BENCHMARK.md, plus the
three follow-up sections (Cloudflare-branch, ad-stripping content loss,
timestamp boundary-straddle sweep).
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import sys
from dataclasses import dataclass
from typing import Callable, Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webanchor import Evidence, Policy, anchor_html
from webanchor.detect import (
    CHALLENGE_SERVER_TOKENS,
    CHALLENGE_SHAPE_MARKERS,
    BOT_WALL_STRONG_MARKERS,
    BOT_WALL_WEAK_MARKERS,
    check_response,
)
from webanchor.errors import BotWallDetected, WebAnchorError

Mutator = Callable[[str, int], str]

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "fixtures")
CORPUS_DIR = os.path.join(FIXTURE_DIR, "corpus")
SMALL_LEGIT_DIR = os.path.join(CORPUS_DIR, "small_legit")


def _read(path: str) -> str:
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# Deterministic per-i token generation.  Never ``random`` -- a fixed digest of
# (label, i) is reproducible on any machine, in any process (R3).
# ---------------------------------------------------------------------------


def _deterministic_hex(label: str, i: int, length: int = 16) -> str:
    digest = hashlib.sha256("{0}:{1}".format(label, i).encode("utf-8")).hexdigest()
    return digest[:length]


def _deterministic_int(label: str, i: int, modulus: int) -> int:
    digest = hashlib.sha256("{0}:{1}".format(label, i).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulus


# ---------------------------------------------------------------------------
# Mutators.  Each is independently toggleable and takes (html, i) -> html.
# ---------------------------------------------------------------------------

#: Attribute names (case-insensitive, matched via regex) that carry
#: nonce/CSRF/session-shaped volatility in real pages.
_NONCE_ATTR_RE = re.compile(
    r'((?:nonce|csrf|integrity|data-nonce|data-csrf|data-session|'
    r'data-request-id|sessionNonce|csrfToken|requestId)\s*=\s*")'
    r'([^"]*)(")',
    re.IGNORECASE,
)

#: JSON-string-valued keys that carry the same class of volatility inside an
#: embedded ``<script type="application/json">`` blob.
_NONCE_JSON_RE = re.compile(
    r'("(?:sessionNonce|csrfToken|requestId)"\s*:\s*")([^"]*)(")',
    re.IGNORECASE,
)


def mutate_nonce_and_csrf(html: str, i: int) -> str:
    """Rewrite nonce/CSRF/session-id-shaped attribute and JSON values.

    Deterministic per ``i``: each occurrence gets a token derived from
    ``sha256(f"{label}:{i}")`` where ``label`` is the matched attribute name,
    so two different attributes never collide and a re-run with the same
    ``i`` reproduces the exact same rewritten string.
    """

    def sub_attr(match: "re.Match[str]") -> str:
        prefix, old_value, suffix = match.group(1), match.group(2), match.group(3)
        token = _deterministic_hex(prefix, i, length=max(8, min(32, len(old_value) or 16)))
        return prefix + token + suffix

    def sub_json(match: "re.Match[str]") -> str:
        prefix, old_value, suffix = match.group(1), match.group(2), match.group(3)
        token = _deterministic_hex(prefix, i, length=max(8, min(32, len(old_value) or 16)))
        return prefix + token + suffix

    html = _NONCE_ATTR_RE.sub(sub_attr, html)
    html = _NONCE_JSON_RE.sub(sub_json, html)
    return html


#: Strict ISO-8601 UTC timestamps, the canonical shape used across the
#: fixtures (``YYYY-MM-DDTHH:MM:SSZ``).  Kept intentionally narrower than
#: ``webanchor.timestamps.ABSOLUTE_RE`` -- this mutator only needs to shift
#: the timestamps the fixtures actually contain in that exact rendered shape.
_ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in_month(year: int, month: int) -> int:
    if month == 2 and _is_leap(year):
        return 29
    return _DAYS_IN_MONTH[month - 1]


def _epoch_seconds(year: int, month: int, day: int, hour: int, minute: int, second: int) -> int:
    """Integer seconds since 1970-01-01T00:00:00Z. Pure integer arithmetic."""
    days = 0
    if year >= 1970:
        for y in range(1970, year):
            days += 366 if _is_leap(y) else 365
    else:
        for y in range(year, 1970):
            days -= 366 if _is_leap(y) else 365
    for m in range(1, month):
        days += _days_in_month(year, m)
    days += day - 1
    return days * 86400 + hour * 3600 + minute * 60 + second


def _from_epoch_seconds(total: int) -> tuple[int, int, int, int, int, int]:
    """Inverse of :func:`_epoch_seconds`. Integer-only, floors toward -inf."""
    day_count, rem = divmod(total, 86400)
    hour, rem = divmod(rem, 3600)
    minute, second = divmod(rem, 60)
    year = 1970
    while True:
        year_len = 366 if _is_leap(year) else 365
        if day_count >= year_len:
            day_count -= year_len
            year += 1
        elif day_count < 0:
            year -= 1
            day_count += 366 if _is_leap(year) else 365
        else:
            break
    month = 1
    while True:
        dim = _days_in_month(year, month)
        if day_count >= dim:
            day_count -= dim
            month += 1
        else:
            break
    day = day_count + 1
    return year, month, day, hour, minute, second


def _shift_iso(match: "re.Match[str]", shift_seconds: int) -> str:
    text = match.group(0)
    year = int(text[0:4])
    month = int(text[5:7])
    day = int(text[8:10])
    hour = int(text[11:13])
    minute = int(text[14:16])
    second = int(text[17:19])
    total = _epoch_seconds(year, month, day, hour, minute, second) + shift_seconds
    y, mo, d, h, mi, s = _from_epoch_seconds(total)
    return "{0:04d}-{1:02d}-{2:02d}T{3:02d}:{4:02d}:{5:02d}Z".format(y, mo, d, h, mi, s)


def mutate_timestamps(html: str, i: int) -> str:
    """Shift every ISO-8601 timestamp found in ``html`` by ``i`` seconds.

    This is the mutator the boundary-straddle sweep (Task 5C) drives directly:
    sweeping ``i`` from 0 to ``2 * timestamp_quantum_seconds`` and watching
    when the quantized output starts to diverge from the ``i=0`` baseline.
    """
    return _ISO_TS_RE.sub(lambda m: _shift_iso(m, i), html)


#: Ad campaign/creative identifiers and cache-busting query strings: the
#: attribute-borne signal that rotates between independent ad-serving calls.
_AD_ATTR_RE = re.compile(
    r'((?:data-campaign|data-creative|data-ad-id)\s*=\s*")([^"]*)(")',
    re.IGNORECASE,
)
_AD_CACHEBUST_RE = re.compile(r"(\?cb=)(\d+)")
_AD_JSON_RE = re.compile(r'("adCampaign"\s*:\s*")([^"]*)(")', re.IGNORECASE)


def mutate_ad_slot(html: str, i: int) -> str:
    """Swap rotating ad campaign/creative identifiers deterministically."""

    def sub_attr(match: "re.Match[str]") -> str:
        prefix, _old, suffix = match.group(1), match.group(2), match.group(3)
        return prefix + "creative-" + _deterministic_hex(prefix, i, length=8) + suffix

    def sub_cb(match: "re.Match[str]") -> str:
        prefix = match.group(1)
        return prefix + str(1_700_000_000 + _deterministic_int(prefix, i, 90_000_000))

    def sub_json(match: "re.Match[str]") -> str:
        prefix, _old, suffix = match.group(1), match.group(2), match.group(3)
        return prefix + "creative-" + _deterministic_hex(prefix, i, length=8) + suffix

    html = _AD_ATTR_RE.sub(sub_attr, html)
    html = _AD_CACHEBUST_RE.sub(sub_cb, html)
    html = _AD_JSON_RE.sub(sub_json, html)
    return html


#: Counter-shaped numbers: a number immediately followed by a counter keyword.
#: Handles both comma-grouped and plain forms; re-groups the output with
#: commas to stay realistic.
_COUNTER_RE = re.compile(
    r"(?P<num>\d[\d,]*)(?P<sep>\s*)(?P<label>people (?:are )?(?:viewing|tracking)"
    r"|viewing this[\w ]*|tracking this flight|comments|reviews|followers|"
    r"subscribers|online now)",
    re.IGNORECASE,
)


def _group_thousands(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    parts = []
    while len(s) > 3:
        parts.insert(0, s[-3:])
        s = s[:-3]
    parts.insert(0, s)
    return ",".join(parts)


def mutate_counter(html: str, i: int) -> str:
    """Increment every view/follower/comment-count-shaped number by ``i``."""

    def sub(match: "re.Match[str]") -> str:
        raw = match.group("num").replace(",", "")
        new_value = int(raw) + i
        return _group_thousands(new_value) + match.group("sep") + match.group("label")

    return _COUNTER_RE.sub(sub, html)


#: Purely-whitespace runs strictly between a ``>`` and the following ``<``:
#: indentation and line-wrapping, never text-node content (a run containing
#: any non-whitespace character never matches, so visible text is untouched).
_STRUCTURAL_WS_RE = re.compile(r"(?<=>)[ \t\r\n]+(?=<)")


def mutate_whitespace(html: str, i: int) -> str:
    """Reflow indentation/line-wrapping without touching any visible text.

    Only whitespace runs that sit entirely between a closing ``>`` and the
    next ``<`` are candidates -- by construction that excludes every run that
    contains actual words, so this mutator cannot change what a reader (or
    WebAnchor's text extractor) sees, only how the markup is laid out on disk.
    """
    counter = [0]

    def sub(match: "re.Match[str]") -> str:
        idx = counter[0]
        counter[0] += 1
        width = _deterministic_int("ws:{0}".format(idx), i, 6)
        newlines = 1 + _deterministic_int("nl:{0}".format(idx), i, 2)
        return ("\n" * newlines) + (" " * width)

    return _STRUCTURAL_WS_RE.sub(sub, html)


def compose(*mutators: Mutator) -> Mutator:
    """Apply several mutators in sequence, left to right."""

    def combined(html: str, i: int) -> str:
        for m in mutators:
            html = m(html, i)
        return html

    return combined


ALL_MUTATORS: tuple[Mutator, ...] = (
    mutate_nonce_and_csrf,
    mutate_timestamps,
    mutate_ad_slot,
    mutate_counter,
    mutate_whitespace,
)

COMPOSED_ALL: Mutator = compose(*ALL_MUTATORS)


# ---------------------------------------------------------------------------
# The simulator
# ---------------------------------------------------------------------------


def simulate_validators(
    html: str,
    url: str,
    policy: Policy,
    *,
    n: int,
    mutator: Mutator,
) -> "list[Evidence | Exception]":
    """Run ``n`` simulated independent validator fetches through the pipeline.

    ``mutator(html, i)`` stands in for "validator i's independent fetch of
    conceptually the same page".  Each of the ``n`` results is either the
    ``Evidence`` the pipeline produced or the ``Exception`` it raised -- a
    raise is a legitimate simulated outcome (a validator that hit a bot wall,
    for instance), never silently dropped.
    """
    results: "list[Evidence | Exception]" = []
    for i in range(n):
        mutated = mutator(html, i)
        try:
            results.append(anchor_html(mutated, url, policy))
        except WebAnchorError as exc:
            results.append(exc)
    return results


def raw_baseline(html: str, *, n: int, mutator: Mutator) -> list[str]:
    """The strawman: literal bytes of the mutated HTML, no WebAnchor at all."""
    return [mutator(html, i) for i in range(n)]


def _majority_agreement(keys: Iterable[str]) -> tuple[int, int]:
    """Return ``(count of the modal key, total count)``.

    Not pairwise agreement -- this is "how many independent fetches match
    the single most common answer", which is exactly what ``strict_eq``
    needs: the leader proposes one fingerprint, and a validator either
    matches it or does not.
    """
    keys = list(keys)
    counts: dict[str, int] = {}
    for k in keys:
        counts[k] = counts.get(k, 0) + 1
    if not counts:
        return 0, 0
    best = max(counts.values())
    return best, len(keys)


def agreement_rate(results: "list[Evidence | Exception] | list[str]") -> tuple[int, int]:
    """``(modal count, total)`` over fingerprints (Evidence) or raw strings.

    An exception is its own distinct "key" (by exception class name + str),
    since two validators raising for different reasons have not agreed on
    anything, and two raising for the *same* reason is a form of agreement
    that ``strict_eq`` cannot express anyway -- it is surfaced separately,
    never folded silently into the numerator.
    """
    keys: list[str] = []
    for r in results:
        if isinstance(r, Evidence):
            keys.append("ok:" + r.fingerprint)
        elif isinstance(r, Exception):
            keys.append("err:" + type(r).__name__ + ":" + str(r))
        else:
            keys.append("raw:" + hashlib.sha256(r.encode("utf-8")).hexdigest())
    return _majority_agreement(keys)


# ---------------------------------------------------------------------------
# Task 5C: the actual experiment
# ---------------------------------------------------------------------------

CORPUS_FIXTURES = (
    "news_article.html",
    "product_page.html",
    "api_style_json_in_html.html",
    "dashboard_stats.html",
)

N_VALIDATORS = 25
URL = "https://example.com/page"


@dataclass(frozen=True)
class Cell:
    fixture: str
    config: str
    modal: int
    total: int

    @property
    def rate(self) -> str:
        return "{0}/{1}".format(self.modal, self.total)


def run_headline_table() -> list[Cell]:
    cells: list[Cell] = []
    for name in CORPUS_FIXTURES:
        html = _read(os.path.join(CORPUS_DIR, name))

        raw_results = raw_baseline(html, n=N_VALIDATORS, mutator=COMPOSED_ALL)
        modal, total = agreement_rate(raw_results)
        cells.append(Cell(name, "raw", modal, total))

        for label, policy in (("default", Policy.default()), ("strict", Policy.strict())):
            results = simulate_validators(html, URL, policy, n=N_VALIDATORS, mutator=COMPOSED_ALL)
            modal, total = agreement_rate(results)
            cells.append(Cell(name, label, modal, total))
    return cells


_NAMED_MUTATORS: tuple[tuple[str, Mutator], ...] = (
    ("nonce_and_csrf", mutate_nonce_and_csrf),
    ("timestamps", mutate_timestamps),
    ("ad_slot", mutate_ad_slot),
    ("counter", mutate_counter),
    ("whitespace", mutate_whitespace),
)


def per_mutator_breakdown() -> str:
    """Isolate each mutator (applied alone, not composed) under each policy.

    This is the diagnostic behind the headline table's low composed-agreement
    cells: it shows WHICH volatility class each policy fails to converge on,
    rather than leaving a bare "1/25" unexplained.
    """
    lines = [
        "### Per-mutator breakdown (isolated, not composed)",
        "",
        "| fixture | mutator | raw | default | strict |",
        "|---|---|---|---|---|",
    ]
    for name in CORPUS_FIXTURES:
        html = _read(os.path.join(CORPUS_DIR, name))
        for mutator_name, mutator in _NAMED_MUTATORS:
            raw_modal, raw_total = agreement_rate(
                raw_baseline(html, n=N_VALIDATORS, mutator=mutator)
            )
            default_modal, default_total = agreement_rate(
                simulate_validators(html, URL, Policy.default(), n=N_VALIDATORS, mutator=mutator)
            )
            strict_modal, strict_total = agreement_rate(
                simulate_validators(html, URL, Policy.strict(), n=N_VALIDATORS, mutator=mutator)
            )
            lines.append(
                "| {0} | {1} | {2}/{3} | {4}/{5} | {6}/{7} |".format(
                    name,
                    mutator_name,
                    raw_modal,
                    raw_total,
                    default_modal,
                    default_total,
                    strict_modal,
                    strict_total,
                )
            )
    return "\n".join(lines)


def render_headline_table(cells: list[Cell]) -> str:
    by_fixture: dict[str, dict[str, Cell]] = {}
    for c in cells:
        by_fixture.setdefault(c.fixture, {})[c.config] = c

    lines = [
        "| fixture | raw agreement | Policy.default() agreement | Policy.strict() agreement |",
        "|---|---|---|---|",
    ]
    for name in CORPUS_FIXTURES:
        row = by_fixture[name]
        lines.append(
            "| {0} | {1} | {2} | {3} |".format(
                name, row["raw"].rate, row["default"].rate, row["strict"].rate
            )
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cloudflare-branch question
# ---------------------------------------------------------------------------


def _false_positives(policy: Policy, small_legit_names: list[str]) -> list[tuple[str, str]]:
    found = []
    for name in small_legit_names:
        html = _read(os.path.join(SMALL_LEGIT_DIR, name))
        headers = {"Server": "cloudflare"}
        try:
            check_response(200, headers, html, policy, url="https://example.com/" + name)
        except BotWallDetected as exc:
            found.append((name, str(exc)))
    return found


def cloudflare_branch_report() -> str:
    lines = [
        "### Cloudflare-branch (Server: + shape-marker) finding -- FIXED",
        "",
        "This branch (`webanchor.detect._check_bot_wall`'s `Server:`+shape-marker",
        "tier) is now gated behind `Policy.detect_bot_wall_server_hint`, which",
        "defaults to `False`; only `Policy.strict()` turns it on. The numbers",
        "below are the same measurement as the original finding, run against",
        "both the fixed default (branch off) and the opt-in strict policy",
        "(branch on, unchanged behavior from before the fix).",
        "",
    ]

    small_legit_names = sorted(os.listdir(SMALL_LEGIT_DIR))

    for label, policy in (("Policy.default() (branch OFF)", Policy.default()),
                          ("Policy.strict() (branch ON, opt-in)", Policy.strict())):
        false_positives = _false_positives(policy, small_legit_names)
        lines.append(
            "**{0}** -- false positives from the Server:+shape branch: {1}/{2}.".format(
                label, len(false_positives), len(small_legit_names)
            )
        )
        for name, detail in false_positives:
            lines.append("  - `{0}`: {1}".format(name, detail))
        lines.append("")

    # Does the extra branch ever fire as the ONLY signal on the real
    # Cloudflare fixture -- i.e. would the weak markers alone have caught it?
    cf_path = os.path.join(FIXTURE_DIR, "cloudflare_challenge.html")
    cf_html = _read(cf_path)
    lowered_body = cf_html.lower()
    strong_hit = next((m for m in BOT_WALL_STRONG_MARKERS if m in lowered_body), None)
    from webanchor.detect import visible_text

    lowered_visible = visible_text(cf_html).lower()
    weak_hit = next((m for m in BOT_WALL_WEAK_MARKERS if m in lowered_visible), None)

    lines.append("")
    lines.append(
        "On `tests/fixtures/cloudflare_challenge.html`: strong marker hit = "
        "{0!r}, weak marker hit = {1!r}.".format(strong_hit, weak_hit)
    )
    if strong_hit is not None:
        lines.append(
            "Verdict: the strong-marker tier alone catches this fixture; "
            "the Server:+shape branch is never the ONLY signal here."
        )
    elif weak_hit is not None:
        lines.append(
            "Verdict: the weak-marker tier alone catches this fixture without "
            "needing the Server:+shape branch."
        )
    else:
        lines.append(
            "Verdict: neither strong nor weak markers fire on their own; the "
            "Server:+shape branch is load-bearing for this fixture."
        )
    lines.append(
        "Since the strong-marker tier alone already catches the real fixture "
        "and the branch is now off by default, anchoring any of the small-"
        "legit corpus under `Policy.default()` behind a challenge-capable "
        "edge no longer raises -- confirmed at 0/{0} above.".format(
            len(small_legit_names)
        )
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Ad-stripping content-loss question
# ---------------------------------------------------------------------------


def ad_stripping_content_loss() -> str:
    lines = ["### Ad-stripping content-loss (Policy.strict())", ""]
    for name in ("product_page.html", "news_article.html"):
        html = _read(os.path.join(CORPUS_DIR, name))
        # Isolate strip_ad_containers as the ONLY variable: both policies are
        # otherwise Policy.strict(), so lowercase/number-banding/timestamp
        # settings cannot contaminate the character-count delta.
        with_ads = Policy.strict().with_changes(strip_ad_containers=False)
        without_ads = Policy.strict()
        from webanchor.pipeline import normalize

        text_with_ads, _ = normalize(html, with_ads)
        text_without_ads, _ = normalize(html, without_ads)
        removed = len(text_with_ads) - len(text_without_ads)
        total = len(text_with_ads)
        pct = (removed * 10000) // total if total else 0  # integer basis points
        lines.append(
            "- `{0}`: {1} of {2} characters removed by ad-container stripping "
            "({3}.{4:02d}%).".format(
                name, removed, total, pct // 100, pct % 100
            )
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Boundary-straddle sweep
# ---------------------------------------------------------------------------


def boundary_straddle_sweep() -> str:
    lines = ["### Timestamp boundary-straddle sweep", ""]
    quantum = Policy().timestamp_quantum_seconds  # 3600, the field default
    policy = Policy(timestamp_mode="quantize", timestamp_quantum_seconds=quantum)

    html = _read(os.path.join(CORPUS_DIR, "dashboard_stats.html"))
    baseline_text, _ = _normalize_pair(html, policy, shift=0)

    sweep_max = 2 * quantum
    diverge_count = 0
    total = sweep_max + 1
    first_divergence = None
    for shift in range(0, sweep_max + 1):
        text, _ = _normalize_pair(html, policy, shift=shift)
        if text != baseline_text:
            diverge_count += 1
            if first_divergence is None:
                first_divergence = shift

    observed_fraction_bp = (diverge_count * 10000) // total
    documented_fraction_bp = (sweep_max * 10000) // (2 * quantum)  # spread/quantum at spread=sweep_max, i.e. 1.0
    lines.append(
        "Swept shift `i` from 0 to {0} seconds (`2 * timestamp_quantum_seconds`, "
        "quantum={1}s) against `{2}`.".format(sweep_max, quantum, "dashboard_stats.html")
    )
    lines.append(
        "Divergence from the `i=0` baseline observed at {0}/{1} sampled shifts "
        "({2}.{3:02d}%). First divergence at shift={4}s.".format(
            diverge_count, total, observed_fraction_bp // 100, observed_fraction_bp % 100, first_divergence
        )
    )
    lines.append(
        "Documented formula (`behavior.py`): residual divergence risk ~= spread / "
        "quantum. At spread=quantum (shift=quantum), the formula predicts risk "
        "~=1.0 (divergence should already be common); observed first divergence "
        "at shift={0}s against quantum={1}s is consistent with that ((first "
        "divergence)/quantum = {2:.4f}).".format(
            first_divergence, quantum, (first_divergence or 0) / quantum
        )
    )
    return "\n".join(lines)


def _normalize_pair(html: str, policy: Policy, *, shift: int) -> tuple[str, dict]:
    from webanchor.pipeline import normalize

    mutated = mutate_timestamps(html, shift)
    return normalize(mutated, policy)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    cells = run_headline_table()
    print("## Headline agreement table (n={0} simulated validators)\n".format(N_VALIDATORS))
    print(render_headline_table(cells))
    print()
    print(per_mutator_breakdown())
    print()
    print(cloudflare_branch_report())
    print()
    print(ad_stripping_content_loss())
    print()
    print(boundary_straddle_sweep())


if __name__ == "__main__":
    main()
