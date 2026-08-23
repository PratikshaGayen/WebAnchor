"""Unit tests for the benchmark tooling in ``tools/corpus_bench.py``.

These test the MUTATORS themselves (determinism, that each changes only what
it claims to, and that ``compose`` applies in order) and the determinism of
``simulate_validators``.  They deliberately do NOT re-run the full
n=25-times-corpus sweep that ``python tools/corpus_bench.py`` performs --
that is a benchmark run, not a unit test, and is verified separately (see
BENCHMARK.md's "how to reproduce" section). This file must stay well under
10 seconds; the slowest thing it does is a handful of ``anchor_html`` calls
over small synthetic strings.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webanchor import Policy
from webanchor.pipeline import normalize

from tools.corpus_bench import (
    compose,
    mutate_ad_slot,
    mutate_counter,
    mutate_nonce_and_csrf,
    mutate_timestamps,
    mutate_whitespace,
    simulate_validators,
)

URL = "https://example.com/page"

SAMPLE_HTML = (
    "<html><head><title>T</title>"
    '<script nonce="abc123">window.x=1;</script></head>'
    "<body>"
    '<div class="ad-slot ad" data-campaign="camp-a" data-nonce="abc123">'
    '<iframe src="https://ads.example.com/x?cb=1714551322">ad</iframe>'
    "</div>"
    "<p>Updated 2024-04-12T00:00:00Z.</p>"
    "<p>58 people are viewing this right now.</p>"
    "<p>Some\n    text\n\n  with   spacing.</p>"
    "</body></html>"
)


# ---------------------------------------------------------------------------
# Determinism: same (html, i) -> same output, every time.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutator",
    [mutate_nonce_and_csrf, mutate_timestamps, mutate_ad_slot, mutate_counter, mutate_whitespace],
)
def test_mutator_is_deterministic(mutator):
    for i in (0, 1, 5, 24):
        first = mutator(SAMPLE_HTML, i)
        second = mutator(SAMPLE_HTML, i)
        assert first == second


def test_mutator_varies_across_i():
    """Sanity check: a mutator that never changes anything would trivially
    pass the determinism test above without doing its job."""
    outputs = {mutate_nonce_and_csrf(SAMPLE_HTML, i) for i in range(5)}
    assert len(outputs) > 1
    outputs = {mutate_counter(SAMPLE_HTML, i) for i in range(5)}
    assert len(outputs) > 1
    outputs = {mutate_ad_slot(SAMPLE_HTML, i) for i in range(5)}
    assert len(outputs) > 1


# ---------------------------------------------------------------------------
# Each mutator changes only what it claims to.
# ---------------------------------------------------------------------------


def test_nonce_and_csrf_touches_only_nonce_like_attrs():
    mutated = mutate_nonce_and_csrf(SAMPLE_HTML, 3)
    # The nonce value changed...
    assert 'nonce="abc123"' not in mutated
    # ...but nothing else in the document did.
    assert "58 people are viewing this right now." in mutated
    assert "2024-04-12T00:00:00Z" in mutated
    assert 'data-campaign="camp-a"' in mutated


def test_nonce_and_csrf_is_invisible_to_pipeline():
    """Attributes never reach extracted text, so this mutator cannot affect
    normalize() output -- it exercises the raw-baseline divergence only."""
    baseline, _ = normalize(SAMPLE_HTML, Policy.default())
    for i in range(5):
        mutated = mutate_nonce_and_csrf(SAMPLE_HTML, i)
        text, _ = normalize(mutated, Policy.default())
        assert text == baseline


def test_timestamps_shifts_only_the_iso_timestamp():
    mutated = mutate_timestamps(SAMPLE_HTML, 3661)  # +1h 1m 1s
    assert "2024-04-12T01:01:01Z" in mutated
    assert "58 people are viewing this right now." in mutated
    assert 'data-campaign="camp-a"' in mutated


def test_timestamps_zero_shift_is_identity():
    assert mutate_timestamps(SAMPLE_HTML, 0) == SAMPLE_HTML


def test_ad_slot_touches_only_campaign_and_cachebust():
    mutated = mutate_ad_slot(SAMPLE_HTML, 7)
    assert 'data-campaign="camp-a"' not in mutated
    assert "cb=1714551322" not in mutated
    assert "58 people are viewing this right now." in mutated
    assert "2024-04-12T00:00:00Z" in mutated


def test_ad_slot_is_invisible_to_pipeline():
    """The mutated attribute and cache-buster live inside dropped subtrees
    (attributes never emit; the ad payload here sits inside <iframe>, which
    is unconditionally dropped), so normalize() output is unaffected."""
    baseline, _ = normalize(SAMPLE_HTML, Policy.default())
    for i in range(5):
        mutated = mutate_ad_slot(SAMPLE_HTML, i)
        text, _ = normalize(mutated, Policy.default())
        assert text == baseline


def test_counter_touches_only_the_counter_number():
    mutated = mutate_counter(SAMPLE_HTML, 4)
    assert "62 people are viewing this right now." in mutated
    assert "2024-04-12T00:00:00Z" in mutated
    assert 'data-campaign="camp-a"' in mutated


def test_counter_is_visible_under_default_policy():
    """Counters are the one mutation class Policy.default() does NOT
    collapse (number_band_mode='none' by default) -- this is the honest,
    documented limitation, not a mutator bug."""
    baseline, _ = normalize(SAMPLE_HTML, Policy.default())
    mutated_text, _ = normalize(mutate_counter(SAMPLE_HTML, 4), Policy.default())
    assert mutated_text != baseline


def test_whitespace_does_not_touch_visible_text():
    mutated = mutate_whitespace(SAMPLE_HTML, 9)
    assert "58 people are viewing this right now." in mutated
    assert "2024-04-12T00:00:00Z" in mutated
    assert "Some\n    text\n\n  with   spacing." in mutated


def test_whitespace_is_invisible_to_pipeline_on_block_level_markup():
    """Reflowing indentation between BLOCK-level (boundary) tags never
    changes normalize() output: collapse_layout's blank-line squeeze and
    per-line strip absorb any indentation style. (A narrower case --
    reflow between two adjacent INLINE elements crossing a 1-vs-2-newline
    threshold -- is not covered by this guarantee and is documented
    separately in BENCHMARK.md; see the news_article.html finding.)"""
    baseline, _ = normalize(SAMPLE_HTML, Policy.default())
    for i in range(10):
        mutated = mutate_whitespace(SAMPLE_HTML, i)
        text, _ = normalize(mutated, Policy.default())
        assert text == baseline


# ---------------------------------------------------------------------------
# compose() applies mutators in order
# ---------------------------------------------------------------------------


def test_compose_applies_in_order():
    calls = []

    def a(html, i):
        calls.append(("a", i))
        return html + "A"

    def b(html, i):
        calls.append(("b", i))
        return html + "B"

    result = compose(a, b)("x", 1)
    assert result == "xAB"
    assert calls == [("a", 1), ("b", 1)]


def test_compose_of_real_mutators_matches_manual_chain():
    combined = compose(mutate_nonce_and_csrf, mutate_counter)(SAMPLE_HTML, 5)
    manual = mutate_counter(mutate_nonce_and_csrf(SAMPLE_HTML, 5), 5)
    assert combined == manual


def test_compose_empty_is_identity():
    assert compose()(SAMPLE_HTML, 3) == SAMPLE_HTML


# ---------------------------------------------------------------------------
# simulate_validators determinism (extends R3 to the benchmark tooling)
# ---------------------------------------------------------------------------


def test_simulate_validators_is_deterministic():
    def mutator(html, i):
        return mutate_counter(mutate_nonce_and_csrf(html, i), i)

    first = simulate_validators(SAMPLE_HTML, URL, Policy.default(), n=8, mutator=mutator)
    second = simulate_validators(SAMPLE_HTML, URL, Policy.default(), n=8, mutator=mutator)

    assert len(first) == len(second) == 8
    for a, b in zip(first, second):
        assert type(a) is type(b)
        if hasattr(a, "fingerprint"):
            assert a.fingerprint == b.fingerprint
            assert a.text == b.text
        else:
            assert str(a) == str(b)


def test_simulate_validators_handles_raises_without_dropping_them():
    """A validator that hits a bot wall is a legitimate simulated outcome,
    not something to silently discard."""
    challenge_html = (
        "<html><body><div id='cf-browser-verification'>"
        "Checking your browser before accessing example.com"
        "</div></body></html>"
    )
    results = simulate_validators(
        challenge_html, URL, Policy.default(), n=3, mutator=lambda h, i: h
    )
    assert len(results) == 3
    assert all(isinstance(r, Exception) for r in results)