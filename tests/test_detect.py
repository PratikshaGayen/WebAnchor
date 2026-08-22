"""Responses that must not reach the LLM -- and, just as hard, ones that must.

Two failure modes are tested with equal weight here.  A library that feeds a
challenge page to a model produces confident wrong verdicts; a library that
refuses valid pages produces nothing at all and gets removed.  The decoy tests
below are not garnish, they are half the specification.
"""

import io
import os

import pytest

from webanchor import Policy
from webanchor.detect import (
    BOT_WALL_MAX_VISIBLE_CHARS,
    BOT_WALL_STRONG_MARKERS,
    BOT_WALL_WEAK_MARKERS,
    CHALLENGE_SERVER_TOKENS,
    CHALLENGE_SHAPE_MARKERS,
    SOFT_ERROR_HEAD_CHARS,
    SOFT_ERROR_MAX_VISIBLE_CHARS,
    SOFT_ERROR_PHRASES,
    check_response,
    visible_text,
)
from webanchor.errors import (
    BotWallDetected,
    ContentTooLarge,
    EmptyContent,
    Forbidden,
    NotFound,
    NotTextual,
    RateLimited,
    SoftErrorPage,
    UnexpectedStatus,
    UpstreamUnavailable,
)

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

DEFAULT = Policy.default()
STRICT = Policy.strict()

GOOD_PAGE = (
    "<html><head><title>Order 100418</title></head><body>"
    "<h1>Order 100418</h1><p>Status: shipped. Total: $1,234.56.</p>"
    "</body></html>"
)


def fixture(name):
    with io.open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as handle:
        return handle.read()


def check(body, status=200, headers=None, policy=None, url="https://e.test/x"):
    return check_response(
        status, headers or {}, body, policy or DEFAULT, url=url
    )


# ---------------------------------------------------------------------------
# The happy path: a usable response returns None and raises nothing
# ---------------------------------------------------------------------------


def test_a_usable_response_returns_none():
    assert check(GOOD_PAGE) is None


@pytest.mark.parametrize("status", [200, 201, 202, 203, 204, 206, 299])
def test_every_2xx_is_accepted(status):
    assert check(GOOD_PAGE, status=status) is None


def test_ordinary_fixtures_are_not_flagged():
    """The existing corpus must survive detection unchanged."""
    for name in sorted(os.listdir(FIXTURE_DIR)):
        if not name.endswith(".html"):
            continue
        if name in ("cloudflare_challenge.html", "soft_404.html"):
            continue
        assert check(fixture(name)) is None, name


# ---------------------------------------------------------------------------
# Check 1: status mapping, delegated to errors.from_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        (429, RateLimited),
        (404, NotFound),
        (403, Forbidden),
        (401, Forbidden),
        (500, UpstreamUnavailable),
        (503, UpstreamUnavailable),
        (302, UnexpectedStatus),
        (418, UnexpectedStatus),
    ],
)
def test_status_errors_are_raised_before_anything_else(status, expected):
    with pytest.raises(expected):
        check(GOOD_PAGE, status=status)


def test_the_status_check_wins_over_the_body_checks():
    """A 429 whose body is also a challenge is reported as the 429.

    The status is the more specific and more actionable fact, and reporting
    the first thing wrong -- rather than the most dramatic -- is what keeps
    two validators raising the same error on the same bytes.
    """
    with pytest.raises(RateLimited):
        check(fixture("cloudflare_challenge.html"), status=429)


def test_the_status_error_carries_the_url():
    with pytest.raises(RateLimited) as info:
        check(GOOD_PAGE, status=429, url="https://shop.test/p/42")
    assert info.value.url == "https://shop.test/p/42"


# ---------------------------------------------------------------------------
# Check 2: bot walls served as HTTP 200 -- the critical one
# ---------------------------------------------------------------------------


def test_the_cloudflare_fixture_is_a_200_and_is_still_rejected():
    """The headline case: status checking alone would pass this straight on."""
    body = fixture("cloudflare_challenge.html")
    with pytest.raises(BotWallDetected) as info:
        check(body, status=200)
    assert info.value.code == "content.bot_wall"


def test_the_bot_wall_error_names_the_marker_that_fired():
    """A typed error that does not say why is only half useful."""
    with pytest.raises(BotWallDetected) as info:
        check(fixture("cloudflare_challenge.html"))
    detail = info.value.detail
    assert any(marker in detail for marker in BOT_WALL_STRONG_MARKERS), detail


def test_the_named_marker_is_the_first_one_present_in_the_body():
    """Determinism in the error path: same bytes, same named marker."""
    body = fixture("cloudflare_challenge.html")
    lowered = body.lower()
    expected = next(m for m in BOT_WALL_STRONG_MARKERS if m in lowered)
    with pytest.raises(BotWallDetected) as info:
        check(body)
    assert repr(expected) in info.value.detail


@pytest.mark.parametrize("marker", BOT_WALL_STRONG_MARKERS)
def test_every_strong_marker_fires_on_its_own(marker):
    body = "<html><body><div>" + marker + "</div></body></html>"
    with pytest.raises(BotWallDetected) as info:
        check(body)
    assert repr(marker) in info.value.detail


@pytest.mark.parametrize("marker", BOT_WALL_STRONG_MARKERS)
def test_a_strong_marker_fires_even_on_a_long_page(marker):
    """Machinery markers are dispositive at any length -- prose never has them."""
    body = "<html><body><p>" + ("filler text. " * 500) + marker + "</p></body></html>"
    with pytest.raises(BotWallDetected):
        check(body)


@pytest.mark.parametrize("marker", BOT_WALL_WEAK_MARKERS)
def test_every_weak_marker_fires_on_a_short_page(marker):
    body = "<html><body><h1>" + marker + "</h1></body></html>"
    with pytest.raises(BotWallDetected) as info:
        check(body)
    assert repr(marker) in info.value.detail


@pytest.mark.parametrize("marker", BOT_WALL_WEAK_MARKERS)
def test_no_weak_marker_fires_on_a_long_page(marker):
    """The length gate, exercised over the whole weak tier rather than one case."""
    filler = (
        "This is an ordinary paragraph of editorial prose about web "
        "infrastructure, long enough to be an article and not a gate. "
    )
    body = (
        "<html><body><p>"
        + filler * 40
        + " The phrase in question is "
        + marker
        + " and it appears here in prose.</p></body></html>"
    )
    assert len(visible_text(body)) > BOT_WALL_MAX_VISIBLE_CHARS
    assert check(body) is None


def test_a_strong_marker_is_matched_in_markup_not_only_in_visible_text():
    """``cf-browser-verification`` is a class name; stripping would lose it."""
    body = '<html><body><div id="cf-browser-verification"></div>' + (
        "<p>" + "content " * 400 + "</p></body></html>"
    )
    assert "cf-browser-verification" not in visible_text(body)
    with pytest.raises(BotWallDetected):
        check(body)


def test_a_weak_marker_hidden_in_a_script_does_not_fire():
    """Weak markers match visible text only; a script string is not a gate."""
    body = (
        "<html><body><script>var msg = 'checking your browser';</script>"
        "<p>Order 100418 shipped.</p></body></html>"
    )
    assert check(body) is None


def test_marker_matching_is_case_insensitive():
    body = "<html><body><h1>JUST A MOMENT...</h1></body></html>"
    with pytest.raises(BotWallDetected):
        check(body)


# --- the Server: header branch ----------------------------------------------
#
# This whole branch is gated behind Policy.detect_bot_wall_server_hint, which
# defaults to False (only Policy.strict() turns it on) -- see BENCHMARK.md's
# "Cloudflare-branch finding": measured on the M5 corpus, this branch had a
# 40% false-positive rate and 0% measured marginal benefit over the strong-
# and weak-marker tiers, which is why it is opt-in rather than default-on.
# The tests below exercise it under STRICT, where it is active; the gate
# itself (silent under DEFAULT, active under STRICT) is tested separately.


@pytest.mark.parametrize("token", CHALLENGE_SERVER_TOKENS)
def test_a_challenge_edge_plus_a_challenge_shaped_short_body_fires(token):
    body = (
        "<html><body><h1>example.com</h1>"
        "<p>Please wait while we direct you.</p></body></html>"
    )
    assert check(body, policy=STRICT) is None  # the body alone is not enough
    with pytest.raises(BotWallDetected) as info:
        check(body, headers={"Server": token}, policy=STRICT)
    assert token in info.value.detail


def test_the_server_header_lookup_is_case_insensitive():
    body = "<html><body><p>One more step</p></body></html>"
    with pytest.raises(BotWallDetected):
        check(body, headers={"SERVER": "cloudflare"}, policy=STRICT)
    with pytest.raises(BotWallDetected):
        check(body, headers={"server": "Cloudflare"}, policy=STRICT)


def test_being_behind_cloudflare_is_not_by_itself_evidence_of_anything():
    """Most of the legitimate web is behind a CDN; that cannot mean 'refuse'."""
    assert check(GOOD_PAGE, headers={"Server": "cloudflare"}, policy=STRICT) is None


def test_a_long_page_behind_cloudflare_with_a_shape_marker_survives():
    body = (
        "<html><body><p>"
        + ("An article about how to enable javascript in your browser. " * 40)
        + "</p></body></html>"
    )
    assert check(body, headers={"Server": "cloudflare"}, policy=STRICT) is None


@pytest.mark.parametrize("marker", CHALLENGE_SHAPE_MARKERS)
def test_every_shape_marker_is_reachable_on_a_challenge_edge(marker):
    body = "<html><body><p>" + marker + "</p></body></html>"
    with pytest.raises(BotWallDetected):
        check(body, headers={"Server": "cloudflare"}, policy=STRICT)


def test_an_unknown_server_header_does_not_lower_the_bar():
    body = "<html><body><p>Please wait</p></body></html>"
    assert check(body, headers={"Server": "nginx/1.24.0"}, policy=STRICT) is None


# --- the opt-in gate itself --------------------------------------------------


@pytest.mark.parametrize("token", CHALLENGE_SERVER_TOKENS)
def test_server_hint_branch_is_silent_under_default_policy(token):
    """Same body, same header, as the STRICT-policy test above -- under
    DEFAULT the branch must never fire, because it is off by construction."""
    body = (
        "<html><body><h1>example.com</h1>"
        "<p>Please wait while we direct you.</p></body></html>"
    )
    assert check(body, headers={"Server": token}) is None


@pytest.mark.parametrize("name", ["coming_soon.html", "redirect_stub.html"])
def test_server_hint_branch_silent_on_the_measured_false_positives(name):
    """The two small-legit fixtures that false-positived in the M5 benchmark
    (BENCHMARK.md, Cloudflare-branch finding) must raise nothing under
    Policy.default() when served behind a challenge-capable edge -- that is
    the entire point of gating the branch off by default."""
    path = os.path.join(FIXTURE_DIR, "corpus", "small_legit", name)
    with io.open(path, encoding="utf-8") as handle:
        body = handle.read()
    assert check_response(200, {"Server": "cloudflare"}, body, DEFAULT) is None


def test_server_hint_branch_still_redundant_on_the_real_cloudflare_fixture():
    """Under STRICT (the branch enabled), the real bot-wall fixture still
    raises -- but the strong-marker tier fires first regardless (see
    test_a_real_captured_cloudflare_challenge_page_raises below and
    BENCHMARK.md), so this is a redundant, not load-bearing, confirmation
    that turning the branch on does not break the case it exists for."""
    path = os.path.join(FIXTURE_DIR, "cloudflare_challenge.html")
    with io.open(path, encoding="utf-8") as handle:
        body = handle.read()
    with pytest.raises(BotWallDetected):
        check_response(200, {"Server": "cloudflare"}, body, STRICT)


# ---------------------------------------------------------------------------
# Check 3: soft error pages
# ---------------------------------------------------------------------------


def test_the_soft_404_fixture_is_rejected():
    with pytest.raises(SoftErrorPage) as info:
        check(fixture("soft_404.html"), status=200)
    assert info.value.code == "content.soft_error"


def test_the_soft_error_error_names_the_phrase_that_fired():
    with pytest.raises(SoftErrorPage) as info:
        check(fixture("soft_404.html"))
    assert any(repr(p) in info.value.detail for p in SOFT_ERROR_PHRASES)


def test_the_soft_error_detail_says_where_the_phrase_was_found():
    with pytest.raises(SoftErrorPage) as info:
        check(fixture("soft_404.html"))
    assert "the page title" in info.value.detail


def test_a_title_only_error_signal_is_enough_on_a_short_page():
    body = (
        "<html><head><title>Service Unavailable</title></head>"
        "<body><p>Please try again later.</p></body></html>"
    )
    with pytest.raises(SoftErrorPage):
        check(body)


def test_an_error_phrase_in_the_head_of_the_body_is_enough():
    body = "<html><body><h1>Something went wrong</h1></body></html>"
    with pytest.raises(SoftErrorPage):
        check(body)


@pytest.mark.parametrize("phrase", SOFT_ERROR_PHRASES)
def test_every_soft_error_phrase_fires_on_a_short_page(phrase):
    body = "<html><body><h1>" + phrase + "</h1></body></html>"
    with pytest.raises(SoftErrorPage) as info:
        check(body)
    assert "characters" in info.value.detail


def test_a_short_page_with_the_phrase_below_the_head_region_survives():
    """Position is a signal, not just presence: real error pages lead with it."""
    lead = "Widget model WX-9 in walnut, ships from our Leeds warehouse. "
    body = (
        "<html><body><p>"
        + lead * 4
        + "Our returns page had an error last week.</p></body></html>"
    )
    visible = visible_text(body)
    assert len(visible) <= SOFT_ERROR_MAX_VISIBLE_CHARS
    assert visible.lower().index("error") > SOFT_ERROR_HEAD_CHARS
    assert check(body) is None


def test_bot_wall_is_checked_before_soft_error():
    """'Access denied' is both; 'this is a challenge' is the useful diagnosis."""
    body = (
        "<html><head><title>Access denied | Error</title></head>"
        "<body><h1>Access denied</h1></body></html>"
    )
    with pytest.raises(BotWallDetected):
        check(body)


# ---------------------------------------------------------------------------
# THE DECOY. False positives are as damaging as false negatives.
# ---------------------------------------------------------------------------


def test_a_long_article_about_404s_and_bot_walls_is_NOT_refused():
    """The test that keeps this library usable.

    The fixture is a genuine editorial article.  It contains ``404`` many
    times, the exact phrases "page not found" and "we will be back", and the
    weak bot-wall marker "checking your browser" in ordinary prose.  Every
    single-signal detector fires on it.  This one must not: a library that
    refuses valid pages is not a safer library, it is an unused one.
    """
    body = fixture("article_about_404s.html")
    lowered = body.lower()
    assert "404" in lowered
    assert "page not found" in lowered
    assert "checking your browser" in lowered
    assert "we will be back" in lowered
    assert len(visible_text(body)) > BOT_WALL_MAX_VISIBLE_CHARS

    assert check(body) is None


def test_the_decoy_article_anchors_end_to_end():
    """Not merely 'does not raise' -- it produces real Evidence."""
    from webanchor import anchor_html

    evidence = anchor_html(
        fixture("article_about_404s.html"), "https://blog.test/404s"
    )
    assert evidence.fingerprint.startswith("wa1:")
    assert "404" in evidence.text


def test_a_long_page_containing_the_word_captcha_survives():
    body = (
        "<html><body><p>"
        + ("Our signup flow uses a captcha to deter automated abuse. " * 40)
        + "</p></body></html>"
    )
    assert check(body) is None


def test_a_long_page_whose_title_says_404_survives():
    body = (
        "<html><head><title>404 pages, considered</title></head><body><p>"
        + ("A discussion of error page design and status semantics. " * 40)
        + "</p></body></html>"
    )
    assert check(body) is None


# ---------------------------------------------------------------------------
# Check 4: textuality
# ---------------------------------------------------------------------------


def test_nul_bytes_are_not_textual():
    with pytest.raises(NotTextual):
        check("<html><body><p>a\x00b</p></body></html>")


def test_control_char_ratio_over_the_policy_limit_is_not_textual():
    body = "<p>" + "abcdefghij" * 10 + "\x01\x02\x03\x04\x05" + "</p>"
    strict_ratio = DEFAULT.with_changes(max_control_char_ratio="0.001")
    with pytest.raises(NotTextual):
        check(body, policy=strict_ratio)
    assert check(body, policy=DEFAULT.with_changes(max_control_char_ratio="0.5")) is None


def test_textuality_uses_the_same_rule_as_the_stripper():
    """One definition of 'textual', shared, so the two cannot drift apart."""
    from webanchor.html_strip import strip_html

    body = "<p>ab\x00cd</p>"
    with pytest.raises(NotTextual):
        check(body)
    with pytest.raises(NotTextual):
        strip_html(body, DEFAULT)


def test_a_bot_wall_is_reported_before_a_textuality_problem():
    body = "<html><body><h1>Just a moment...</h1>\x01\x01\x01</body></html>"
    with pytest.raises(BotWallDetected):
        check(body, policy=DEFAULT.with_changes(max_control_char_ratio="0"))


# ---------------------------------------------------------------------------
# Check 5: size
# ---------------------------------------------------------------------------


def test_an_oversized_response_is_rejected():
    policy = DEFAULT.with_changes(max_content_bytes=50)
    with pytest.raises(ContentTooLarge):
        check("<p>" + "x" * 500 + "</p>", policy=policy)


def test_the_size_limit_counts_utf8_bytes_not_characters():
    body = "<p>" + "你" * 40 + "</p>"  # 3 bytes each
    assert len(body) < 100
    with pytest.raises(ContentTooLarge):
        check(body, policy=DEFAULT.with_changes(max_content_bytes=100))
    assert check(body, policy=DEFAULT.with_changes(max_content_bytes=200)) is None


# ---------------------------------------------------------------------------
# Check 6: emptiness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("body", ["", " ", "\n\n", "\t \r\n "])
def test_empty_or_whitespace_bodies_are_rejected(body):
    with pytest.raises(EmptyContent):
        check(body)


def test_an_empty_body_that_is_also_a_challenge_reports_the_challenge():
    """Emptiness is last on purpose: the challenge is the actionable fact."""
    body = "<!-- x --><div class=cf-browser-verification></div>"
    with pytest.raises(BotWallDetected):
        check(body)


# ---------------------------------------------------------------------------
# R3 / R6 structure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table",
    [
        BOT_WALL_STRONG_MARKERS,
        BOT_WALL_WEAK_MARKERS,
        CHALLENGE_SERVER_TOKENS,
        CHALLENGE_SHAPE_MARKERS,
        SOFT_ERROR_PHRASES,
    ],
)
def test_every_marker_table_is_a_tuple_not_a_set(table):
    """R3: set iteration order is not a stable contract across builds."""
    assert isinstance(table, tuple)
    assert table, "an empty marker table would silently disable a check"


@pytest.mark.parametrize(
    "table",
    [
        BOT_WALL_STRONG_MARKERS,
        BOT_WALL_WEAK_MARKERS,
        CHALLENGE_SERVER_TOKENS,
        CHALLENGE_SHAPE_MARKERS,
        SOFT_ERROR_PHRASES,
    ],
)
def test_every_marker_is_lowercase_so_matching_is_symmetric(table):
    for marker in table:
        assert marker == marker.lower(), marker


@pytest.mark.parametrize(
    "table",
    [BOT_WALL_STRONG_MARKERS, BOT_WALL_WEAK_MARKERS, SOFT_ERROR_PHRASES],
)
def test_no_marker_table_contains_duplicates(table):
    assert len(set(table)) == len(table)


def test_the_marker_tables_are_documented_as_behavior_versioned():
    """R6: adding a marker changes output, so it must reach policy_id."""
    from webanchor import behavior

    assert "detect.py" in behavior.__doc__
    assert "BOT_WALL_STRONG_MARKERS" in behavior.__doc__


def test_detection_is_deterministic_over_repeated_runs():
    bodies = [
        GOOD_PAGE,
        fixture("cloudflare_challenge.html"),
        fixture("soft_404.html"),
        fixture("article_about_404s.html"),
    ]
    for body in bodies:
        outcomes = []
        for _ in range(20):
            try:
                check(body)
                outcomes.append(None)
            except Exception as exc:
                outcomes.append((type(exc), str(exc)))
        assert len(set(map(repr, outcomes))) == 1


# ---------------------------------------------------------------------------
# visible_text: a total function, by contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body", ["", "   ", "<p>", "<<>>", "\x00", "<script>x</script>", "plain"]
)
def test_visible_text_never_raises(body):
    """It runs before every other check, so it must be total."""
    assert isinstance(visible_text(body), str)


def test_visible_text_drops_script_style_and_comment_bodies():
    body = (
        "<!-- hidden --><style>p{color:red}</style>"
        "<script>var a = 'x';</script><p>Kept</p>"
    )
    assert visible_text(body) == "Kept"


def test_visible_text_collapses_whitespace():
    assert visible_text("<p>a\n\n   b</p>") == "a b"
