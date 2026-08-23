"""``anchor`` and ``anchor_html``: the top-level API, end to end.

The claim being tested is the product claim: two captures of one page that
differ in every volatile way produce the SAME ``Evidence.fingerprint``, and a
response that must not reach an LLM raises instead of producing one.
"""

import io
import os
import sys

import pytest

from webanchor import Evidence, Policy, anchor, anchor_html, fingerprint
from webanchor.errors import (
    BotWallDetected,
    ContentTooLarge,
    EmptyContent,
    NotFound,
    NotTextual,
    RateLimited,
    SoftErrorPage,
    WebAnchorError,
)
from webanchor.pipeline import normalize

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

DEFAULT = Policy.default()
STRICT = Policy.strict()
URL = "https://shop.example.com/product/42"


def fixture(name):
    with io.open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# THE MONEY TEST, now at the top-level API
# ---------------------------------------------------------------------------


def test_two_captures_of_one_page_share_a_fingerprint():
    """The whole thesis of the library, asserted through the public entry point.

    ``volatile_a`` and ``volatile_b`` are two captures of one page separated
    by the things that separate a leader's fetch from a validator's: a ticking
    timestamp, a rotated ad, a fresh nonce, a changed session id.  Raw, they
    do not match. Anchored, they are one fact.
    """
    a = anchor_html(fixture("volatile_a.html"), URL, STRICT)
    b = anchor_html(fixture("volatile_b.html"), URL, STRICT)
    assert a.fingerprint == b.fingerprint
    assert a.text == b.text
    assert a.to_calldata() == b.to_calldata()


def test_the_raw_captures_really_do_differ():
    """The control: without WebAnchor there is nothing for strict_eq to agree on."""
    assert fixture("volatile_a.html") != fixture("volatile_b.html")


def test_convergence_holds_under_the_default_policy_too():
    a = anchor_html(fixture("volatile_a.html"), URL, DEFAULT)
    b = anchor_html(fixture("volatile_b.html"), URL, DEFAULT)
    assert a.fingerprint == b.fingerprint


def test_five_simulated_validators_agree():
    """A preview of the benchmark corpus: N independent anchors, one fingerprint."""
    captures = [fixture("volatile_a.html"), fixture("volatile_b.html")] * 3
    prints = {anchor_html(html, URL, STRICT).fingerprint for html in captures[:5]}
    assert len(prints) == 1


# ---------------------------------------------------------------------------
# anchor_html: the shape of the result
# ---------------------------------------------------------------------------


def test_anchor_html_returns_a_populated_evidence():
    evidence = anchor_html("<p>Order 100418 shipped.</p>", URL)
    assert isinstance(evidence, Evidence)
    assert evidence.url == URL
    assert evidence.status == 200
    assert evidence.policy_id == DEFAULT.policy_id
    assert evidence.fingerprint.startswith("wa1:")
    assert "100418" in evidence.text


def test_the_fingerprint_is_the_one_the_pure_path_would_produce():
    """No hidden inputs: Evidence.fingerprint is fingerprint(normalize(...))."""
    html = fixture("volatile_a.html")
    for policy in (DEFAULT, STRICT):
        text, bands = normalize(html, policy)
        evidence = anchor_html(html, URL, policy)
        assert evidence.text == text
        assert evidence.bands == bands
        assert evidence.fingerprint == fingerprint(text, policy.policy_id)


def test_the_default_policy_is_resolved_at_call_time():
    assert anchor_html("<p>x</p>", URL).policy_id == Policy.default().policy_id
    assert anchor_html("<p>x</p>", URL, None).policy_id == Policy.default().policy_id


def test_an_explicit_policy_changes_the_fingerprint_visibly():
    """R5: different policies must produce visibly different fingerprints."""
    html = fixture("volatile_a.html")
    a = anchor_html(html, URL, DEFAULT)
    b = anchor_html(html, URL, STRICT)
    assert a.fingerprint != b.fingerprint
    assert a.policy_id != b.policy_id


def test_a_supplied_status_is_carried_onto_the_evidence():
    evidence = anchor_html("<p>ok</p>", URL, status=203)
    assert evidence.status == 203


def test_headers_are_optional_and_default_to_empty():
    assert anchor_html("<p>ok</p>", URL, headers=None).status == 200
    assert anchor_html("<p>ok</p>", URL, headers={"Server": "nginx"}).status == 200


def test_evidence_is_calldata_safe():
    payload = anchor_html("<p>Total 1234</p>", URL, STRICT).to_calldata()
    for value in payload.values():
        assert isinstance(value, (str, int, bool, dict))
    assert list(payload) == sorted(payload)


# ---------------------------------------------------------------------------
# fetched_bucket: the library never reads a clock
# ---------------------------------------------------------------------------


def test_fetched_bucket_defaults_to_zero_and_is_never_invented():
    """A library that reads wall-clock time to build a consensus artifact is
    self-defeating: the reading differs on every validator and goes straight
    into the artifact they are supposed to agree on."""
    assert anchor_html("<p>x</p>", URL).fetched_bucket == 0


def test_fetched_bucket_is_accepted_from_the_caller():
    evidence = anchor_html("<p>x</p>", URL, fetched_bucket=1_700_000_000)
    assert evidence.fetched_bucket == 1_700_000_000
    assert evidence.to_calldata()["fetched_bucket"] == 1_700_000_000


def test_two_anchors_of_one_page_agree_without_a_shared_bucket():
    """Default bucket 0 is shared by construction, so it cannot diverge."""
    a = anchor_html(fixture("volatile_a.html"), URL, STRICT)
    b = anchor_html(fixture("volatile_b.html"), URL, STRICT)
    assert a.fetched_bucket == b.fetched_bucket == 0
    assert a.to_calldata() == b.to_calldata()


def test_the_docstring_explains_why_the_clock_is_the_callers_problem():
    doc = anchor_html.__doc__
    assert "never reads a clock" in doc
    assert "self-defeating" in doc


def test_no_module_in_the_package_reads_the_wall_clock():
    """Structural, not by review: a clock read anywhere would poison consensus."""
    import ast

    pkg = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webanchor"
    )
    banned = {"time", "monotonic", "time_ns", "now", "today", "utcnow"}
    for name in sorted(os.listdir(pkg)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(pkg, name)
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in banned, "{0}:{1} calls {2}()".format(
                    name, node.lineno, node.func.attr
                )


# ---------------------------------------------------------------------------
# Detection is in the default path, not behind a flag
# ---------------------------------------------------------------------------


def test_a_bot_wall_raises_instead_of_producing_evidence():
    with pytest.raises(BotWallDetected) as info:
        anchor_html(fixture("cloudflare_challenge.html"), URL)
    assert info.value.url == URL
    assert info.value.detail


def test_a_soft_error_page_raises_instead_of_producing_evidence():
    with pytest.raises(SoftErrorPage):
        anchor_html(fixture("soft_404.html"), URL)


def test_a_hard_status_raises_before_anything_is_normalized():
    with pytest.raises(RateLimited):
        anchor_html("<p>fine content</p>", URL, status=429)
    with pytest.raises(NotFound):
        anchor_html("<p>fine content</p>", URL, status=404)


@pytest.mark.parametrize(
    "html,policy,expected",
    [
        ("", DEFAULT, EmptyContent),
        ("   ", DEFAULT, EmptyContent),
        ("<p>a\x00b</p>", DEFAULT, NotTextual),
        ("<p>" + "x" * 500 + "</p>", DEFAULT.with_changes(max_content_bytes=50),
         ContentTooLarge),
    ],
)
def test_unusable_content_raises_rather_than_returning_partial_evidence(
    html, policy, expected
):
    """R4: a partial Evidence is indistinguishable from a real one in calldata."""
    with pytest.raises(expected):
        anchor_html(html, URL, policy)


def test_the_decoy_article_still_anchors():
    """The false-positive guard, at the top-level API."""
    evidence = anchor_html(fixture("article_about_404s.html"), URL)
    assert evidence.fingerprint.startswith("wa1:")


# ---------------------------------------------------------------------------
# anchor(): the same path, with a network in front of it
# ---------------------------------------------------------------------------


def test_importing_webanchor_works_with_genlayer_absent():
    import webanchor

    assert "genlayer" not in sys.modules
    assert callable(webanchor.anchor)
    assert callable(webanchor.anchor_html)
    with pytest.raises(ImportError):
        __import__("genlayer")


def test_anchor_only_fails_when_it_is_actually_called():
    """The deferral that keeps the library importable off-chain (R2)."""
    with pytest.raises(WebAnchorError) as info:
        anchor("https://example.test/")
    assert "GenVM" in info.value.detail
    assert "anchor_html" in info.value.detail


def test_anchor_and_anchor_html_are_exported_from_the_package():
    import webanchor

    assert "anchor" in webanchor.__all__
    assert "anchor_html" in webanchor.__all__
    assert webanchor.anchor is anchor
    assert webanchor.anchor_html is anchor_html


def test_anchor_delegates_to_anchor_html_over_a_fake_sdk(monkeypatch):
    """They must be one code path, not two that resemble each other."""
    from tests.test_fetch import _FakeResponse, _FakeWeb, install_fake_gl

    html = fixture("volatile_a.html")
    web = _FakeWeb(get_result=_FakeResponse(200, {"Server": "nginx"}, html.encode()))
    install_fake_gl(monkeypatch, web)

    fetched = anchor("https://example.test/p", STRICT, mode="get")
    offline = anchor_html(html, "https://example.test/p", STRICT)
    assert fetched.fingerprint == offline.fingerprint
    assert fetched.to_calldata() == offline.to_calldata()


def test_anchor_passes_the_fetched_bucket_through(monkeypatch):
    from tests.test_fetch import _FakeWeb, install_fake_gl

    web = _FakeWeb(render_result="<p>Order 100418 shipped.</p>")
    install_fake_gl(monkeypatch, web)
    evidence = anchor("https://example.test/p", fetched_bucket=99)
    assert evidence.fetched_bucket == 99


def test_anchor_applies_detection_to_what_it_fetched(monkeypatch):
    from tests.test_fetch import _FakeWeb, install_fake_gl

    web = _FakeWeb(render_result=fixture("cloudflare_challenge.html"))
    install_fake_gl(monkeypatch, web)
    with pytest.raises(BotWallDetected):
        anchor("https://example.test/p")


def test_anchor_reports_a_hard_status_from_get_mode(monkeypatch):
    from tests.test_fetch import _FakeResponse, _FakeWeb, install_fake_gl

    web = _FakeWeb(get_result=_FakeResponse(429, {}, b"<p>slow down</p>"))
    install_fake_gl(monkeypatch, web)
    with pytest.raises(RateLimited):
        anchor("https://example.test/p", mode="get")


def test_pipeline_does_not_import_fetch_at_module_scope():
    """R2, structurally: pipeline is imported by ``webanchor/__init__``."""
    import ast

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "webanchor",
        "pipeline.py",
    )
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert node.module != "fetch"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["volatile_a.html", "volatile_b.html", "simple.html", "entities.html"]
)
def test_anchor_html_is_deterministic_over_repeated_runs(name):
    html = fixture(name)
    first = anchor_html(html, URL, STRICT).to_calldata()
    for _ in range(25):
        assert anchor_html(html, URL, STRICT).to_calldata() == first


def test_fresh_policy_objects_give_identical_evidence():
    html = fixture("volatile_a.html")
    a = anchor_html(html, URL, Policy.strict())
    b = anchor_html(html, URL, Policy.strict())
    assert a.to_calldata() == b.to_calldata()
