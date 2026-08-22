"""M2: the volatile-DOM stripper.

The centrepiece is :func:`test_MONEY_two_validator_fetches_of_the_same_page_converge`.
Everything else in this file exists to prove that the money test is not passing
by accident.
"""

import io
import os

import pytest

from webanchor import Policy, fingerprint
from webanchor.errors import ContentTooLarge, EmptyContent, NotTextual
from webanchor.html_strip import strip_html
from webanchor.policy import DEFAULT_MAX_TAG_DEPTH

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

FIXTURE_NAMES = (
    "simple.html",
    "malformed.html",
    "scripts.html",
    "entities.html",
    "adverts.html",
    "volatile_a.html",
    "volatile_b.html",
)


def fixture(name):
    with io.open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as handle:
        return handle.read()


DEFAULT = Policy.default()
STRICT = Policy.strict()


def test_every_declared_fixture_exists():
    for name in FIXTURE_NAMES:
        assert os.path.isfile(os.path.join(FIXTURE_DIR, name)), name


# ---------------------------------------------------------------------------
# THE MONEY TEST
# ---------------------------------------------------------------------------


def test_MONEY_two_validator_fetches_of_the_same_page_converge():
    """The entire thesis of WebAnchor, in one assertion.

    ``volatile_a.html`` and ``volatile_b.html`` are two captures of the *same*
    page taken moments apart, exactly as a leader and a validator would see it.
    They differ in a script nonce, a CSRF hidden-input value, an HTML comment
    build hash, a script body carrying a render timestamp, and a rotating ad
    slot.  Raw, they will never ``strict_eq``.  Through ``strip_html`` under the
    CONSERVATIVE default policy they must be byte-identical -- and so must
    their fingerprints, which is what consensus actually compares.
    """
    raw_a = fixture("volatile_a.html")
    raw_b = fixture("volatile_b.html")

    assert raw_a != raw_b, "the fixture pair must actually differ"

    text_a = strip_html(raw_a, DEFAULT)
    text_b = strip_html(raw_b, DEFAULT)

    assert text_a == text_b
    assert fingerprint(text_a, DEFAULT.policy_id) == fingerprint(
        text_b, DEFAULT.policy_id
    )

    # And the stable prose really is still there -- convergence by deleting
    # everything would be a cheat.
    assert "Order 100418 was shipped on 12 April 2024." in text_a
    assert "Northwind Trading Company" in text_a


def test_MONEY_pair_diverges_before_stripping():
    """Guard against a fixture pair that was accidentally made identical."""
    raw_a = fixture("volatile_a.html")
    raw_b = fixture("volatile_b.html")
    assert fingerprint(raw_a, DEFAULT.policy_id) != fingerprint(
        raw_b, DEFAULT.policy_id
    )


@pytest.mark.parametrize(
    "token",
    [
        "n0nceAAAAAAAAAAA1",
        "z9onceBBBBBBBBBB2",
        "AAAA1111bbbb2222cccc3333",
        "ZZZZ9999yyyy8888xxxx7777",
        "4f1c9a2e0b",
        "91de77c503",
        "web-07",
        "web-13",
        "__RENDERED_AT__",
        "req-aaaaaaaa-1111",
        "spring-a",
        "Rotating creative A",
        "1714551322",
    ],
)
def test_no_volatile_token_survives(token):
    combined = strip_html(fixture("volatile_a.html"), DEFAULT) + strip_html(
        fixture("volatile_b.html"), DEFAULT
    )
    assert token not in combined


# ---------------------------------------------------------------------------
# Script / style / comment removal
# ---------------------------------------------------------------------------


def test_script_and_style_bodies_never_reach_the_output():
    text = strip_html(fixture("scripts.html"), DEFAULT)
    assert "ZZSCRIPTTOKENZZ" not in text
    assert "ZZSTYLETOKENZZ" not in text
    assert "ZZNOSCRIPTTOKENZZ" not in text
    assert "console.log" not in text
    assert "Visible Headline" in text


def test_closing_div_inside_a_js_string_does_not_corrupt_parsing():
    """A regex tag-stripper mis-nests here; ``html.parser`` CDATA mode does not."""
    text = strip_html(fixture("scripts.html"), DEFAULT)
    assert "this is inside a JS string" not in text
    assert "Visible paragraph after the script trap." in text
    assert "Last visible paragraph." in text


def test_inline_script_trap_does_not_swallow_following_content():
    html = (
        "<p>before</p>"
        '<script>var s = "</div><p>swallowed</p>";</script>'
        "<p>after</p>"
    )
    text = strip_html(html, DEFAULT)
    assert "swallowed" not in text
    assert "before" in text and "after" in text


def test_comments_are_removed_including_the_build_hash():
    text = strip_html(fixture("scripts.html"), DEFAULT)
    assert "ZZBUILDHASHZZ" not in text
    assert "build:" not in text
    assert "web-07" not in text


def test_comments_survive_when_the_policy_says_so():
    policy = DEFAULT.with_changes(strip_comments=False)
    text = strip_html("<p>x</p><!-- ZZBUILDHASHZZ -->", policy)
    assert "ZZBUILDHASHZZ" in text


def test_script_body_survives_when_strip_scripts_is_off():
    policy = DEFAULT.with_changes(strip_scripts=False)
    text = strip_html("<p>x</p><script>var m='ZZSCRIPTTOKENZZ';</script>", policy)
    assert "ZZSCRIPTTOKENZZ" in text


@pytest.mark.parametrize(
    "tag", ["noscript", "template", "svg", "canvas", "iframe", "object", "applet"]
)
def test_unconditional_drop_tags_are_dropped_under_every_policy(tag):
    html = "<p>keep</p><{0}>ZZDROPPEDZZ</{0}><p>keep2</p>".format(tag)
    for policy in (DEFAULT, STRICT, DEFAULT.with_changes(strip_scripts=False)):
        text = strip_html(html, policy)
        assert "ZZDROPPEDZZ" not in text
        assert "keep" in text and "keep2" in text


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------


def test_malformed_html_does_not_raise_and_yields_visible_text():
    text = strip_html(fixture("malformed.html"), DEFAULT)
    assert text == (
        "Broken But Readable\n"
        "\n"
        "First paragraph never closes.\n"
        "\n"
        "Second paragraph also never closes.\n"
        "Mismatched nesting here.\n"
        "\n"
        "Inside an unclosed div."
    )


@pytest.mark.parametrize(
    "html",
    [
        "<p>a",
        "</span>text",
        "<div><p>a</div></p>",
        "<b><i>x</b></i>",
        "<div class=unquoted>y</div>",
        "<p>a<p>b<p>c",
        "<<p>weird</p>",
        "<p title='<'>angle</p>",
        "</p></div></body></html>stray",
    ],
)
def test_malformed_shapes_never_raise(html):
    assert strip_html(html, DEFAULT)


# ---------------------------------------------------------------------------
# Structural boundaries
# ---------------------------------------------------------------------------


def test_adjacent_paragraphs_do_not_fuse():
    text = strip_html("<p>a</p><p>b</p>", DEFAULT)
    assert "ab" not in text
    assert text.split("\n")[0] == "a"
    assert text.split("\n")[-1] == "b"


@pytest.mark.parametrize(
    "html",
    [
        "<div>a</div><div>b</div>",
        "<li>a</li><li>b</li>",
        "<td>a</td><td>b</td>",
        "<h1>a</h1><h2>b</h2>",
        "a<br>b",
        "a<hr>b",
        "<section>a</section><article>b</article>",
        "<dt>a</dt><dd>b</dd>",
        "<summary>a</summary><figcaption>b</figcaption>",
    ],
)
def test_block_boundaries_separate_text(html):
    assert "ab" not in strip_html(html, DEFAULT)


@pytest.mark.parametrize(
    "html",
    [
        "<span>a</span><span>b</span>",
        "<b>a</b><i>b</i>",
        "<em>a</em><strong>b</strong>",
        "<a href='x'>a</a><a href='y'>b</a>",
    ],
)
def test_inline_tags_do_not_introduce_boundaries(html):
    assert strip_html(html, DEFAULT) == "ab"


def test_no_more_than_two_consecutive_newlines():
    html = "<div><div><div><p>a</p></div></div></div><div><p>b</p></div>"
    text = strip_html(html, DEFAULT)
    assert "\n\n\n" not in text


def test_no_line_has_trailing_whitespace():
    text = strip_html(fixture("simple.html"), DEFAULT)
    for line in text.split("\n"):
        assert line == line.rstrip()
    assert text == text.strip()


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


def test_entities_are_unescaped():
    text = strip_html(fixture("entities.html"), DEFAULT)
    assert "Tom & Jerry" in text
    assert "5 < 6 and 7 > 6" in text
    assert 'quote "quoted" quote' in text
    assert "dash — dash" in text
    assert "café 你好 — naïve — Ω" in text


def test_nbsp_collapses_to_an_ordinary_space():
    text = strip_html(fixture("entities.html"), DEFAULT)
    assert "gap: here" in text
    assert " " not in text, "a non-breaking space is an invisible divergence"


def test_unknown_entity_is_left_alone_and_gains_no_semicolon():
    text = strip_html("<p>AT&T and R&D</p>", DEFAULT)
    assert text == "AT&T and R&D"


def test_double_escaped_entity_is_unescaped_exactly_once():
    text = strip_html("<p>&amp;lt;</p>", DEFAULT)
    assert text == "&lt;"


# ---------------------------------------------------------------------------
# Ad containers: whole-token matching, and a genuinely conservative default
# ---------------------------------------------------------------------------


def test_conservative_default_keeps_all_four_ad_fixture_blocks():
    text = strip_html(fixture("adverts.html"), DEFAULT)
    for token in (
        "ZZADSLOTZZ",
        "ZZSPONSOREDZZ",
        "ZZDOWNLOADZZ",
        "ZZLOADINGZZ",
    ):
        assert token in text, token
    assert "Real editorial content that must always survive." in text


def test_aggressive_mode_drops_ads_but_the_decoys_survive():
    """Whole-token matching, proven.

    ``download`` contains the substring ``ad``; ``loading`` contains it too.
    A substring matcher deletes both and silently loses real content.
    """
    text = strip_html(fixture("adverts.html"), STRICT)
    assert "ZZADSLOTZZ" not in text
    assert "ZZSPONSOREDZZ" not in text
    assert "ZZDOWNLOADZZ" in text
    assert "ZZLOADINGZZ" in text
    assert "Real editorial content that must always survive." in text
    assert "Closing editorial paragraph." in text


@pytest.mark.parametrize(
    "attrs",
    [
        'class="ad"',
        'class="ad-slot"',
        'class="slot ads"',
        'class="promo_box"',
        'id="sponsored-1"',
        'id="banner"',
        'CLASS="AD-SLOT"',
        'class="wrapper advertisement inner"',
        'id="promoted_2"',
    ],
)
def test_ad_tokens_are_matched_case_insensitively_in_class_and_id(attrs):
    html = "<p>keep</p><div {0}>ZZADZZ</div>".format(attrs)
    assert "ZZADZZ" not in strip_html(html, STRICT)
    assert "ZZADZZ" in strip_html(html, DEFAULT)


@pytest.mark.parametrize(
    "attrs",
    [
        'class="download"',
        'id="loading"',
        'class="header"',
        'class="upload"',
        'id="thread"',
        'class="badge"',
        'class="gradient"',
        'id="road-map"',
        'class="adjacent"',
    ],
)
def test_substring_lookalikes_survive_even_in_aggressive_mode(attrs):
    html = "<div {0}>ZZKEEPZZ</div>".format(attrs)
    assert "ZZKEEPZZ" in strip_html(html, STRICT)
    assert "ZZKEEPZZ" in strip_html(html, DEFAULT)


def test_ad_patterns_are_configurable_and_only_apply_when_enabled():
    enabled = DEFAULT.with_changes(
        strip_ad_containers=True, ad_container_patterns=("widgetbox",)
    )
    disabled = DEFAULT.with_changes(
        strip_ad_containers=False, ad_container_patterns=("widgetbox",)
    )
    html = '<div class="widgetbox">ZZCUSTOMZZ</div><div class="ad">ZZADZZ</div>'
    text_on = strip_html(html, enabled)
    assert "ZZCUSTOMZZ" not in text_on
    assert "ZZADZZ" in text_on, "patterns replace the defaults, they do not extend"
    assert "ZZCUSTOMZZ" in strip_html(html, disabled)


def test_ad_container_drop_takes_the_whole_subtree():
    html = '<div class="ad"><p>x</p><div><span>ZZDEEPZZ</span></div></div><p>keep</p>'
    text = strip_html(html, STRICT)
    assert "ZZDEEPZZ" not in text
    assert text == "keep"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURE_NAMES)
@pytest.mark.parametrize("policy", [DEFAULT, STRICT], ids=["default", "strict"])
def test_stripping_is_idempotent(name, policy):
    once = strip_html(fixture(name), policy)
    twice = strip_html(once, policy)
    assert twice == once
    assert strip_html(twice, policy) == once


# ---------------------------------------------------------------------------
# Typed errors (R4)
# ---------------------------------------------------------------------------


def test_content_too_large_raises():
    policy = DEFAULT.with_changes(max_content_bytes=100)
    with pytest.raises(ContentTooLarge) as info:
        strip_html("<p>" + "x" * 500 + "</p>", policy)
    assert info.value.code == "content.too_large"


def test_content_at_the_limit_is_accepted():
    html = "<p>abcdefg</p>"
    policy = DEFAULT.with_changes(max_content_bytes=len(html.encode("utf-8")))
    assert strip_html(html, policy) == "abcdefg"


def test_multibyte_size_is_measured_in_bytes_not_characters():
    html = "<p>" + "€" * 40 + "</p>"
    policy = DEFAULT.with_changes(max_content_bytes=60)
    assert len(html) < 60 < len(html.encode("utf-8"))
    with pytest.raises(ContentTooLarge):
        strip_html(html, policy)


@pytest.mark.parametrize(
    "html",
    [
        "",
        "   ",
        "\n\n\t",
        "<html><body></body></html>",
        "<div></div><div></div>",
        "<script>var only = 1;</script>",
        "<!-- only a comment -->",
        "<style>.a{color:red}</style>",
    ],
)
def test_empty_content_raises(html):
    with pytest.raises(EmptyContent) as info:
        strip_html(html, DEFAULT)
    assert info.value.code == "content.empty"


def test_empty_result_is_never_returned_as_an_empty_string():
    """R4: an empty page must be loud, not an empty string handed to an LLM."""
    try:
        result = strip_html("<div></div>", DEFAULT)
    except EmptyContent:
        return
    pytest.fail("strip_html returned {0!r} instead of raising".format(result))


@pytest.mark.parametrize(
    "html",
    [
        "<p>a\x00b</p>",
        "\x00",
        "<p>ok</p>\x00",
        "\x01\x02\x03\x04\x05\x06<p>x</p>",
        "".join(chr(i % 32) for i in range(200)),
    ],
)
def test_not_textual_raises(html):
    with pytest.raises(NotTextual) as info:
        strip_html(html, DEFAULT)
    assert info.value.code == "content.not_textual"


def test_tabs_newlines_and_carriage_returns_are_not_control_noise():
    html = "<p>a</p>\r\n\t<p>b</p>\r\n\t"
    assert strip_html(html, DEFAULT) == "a\n\nb"


def test_non_string_input_raises_typed_error_not_typeerror():
    with pytest.raises(NotTextual):
        strip_html(b"<p>x</p>", DEFAULT)


# ---------------------------------------------------------------------------
# Pathological input
# ---------------------------------------------------------------------------


def test_deeply_nested_html_does_not_recurse_or_raise():
    depth = 5000
    html = "<div>" * depth + "deep content" + "</div>" * depth
    text = strip_html(html, DEFAULT)
    assert text == "deep content"


def test_deep_nesting_without_closing_tags_is_bounded():
    html = "<div>" * 5000 + "tail"
    assert strip_html(html, DEFAULT) == "tail"


def test_depth_cap_is_the_documented_value():
    """R6: the cap moved from a module constant onto Policy, value unchanged.

    A module-level cap could differ between two WebAnchor versions without
    changing any policy_id, so one validator would truncate a deep page where
    another did not -- silently.  On Policy it reaches the policy_id.
    """
    assert DEFAULT_MAX_TAG_DEPTH == 1000
    assert Policy.default().max_tag_depth == 1000
    assert DEFAULT.max_tag_depth == 1000


def test_deeply_nested_drop_tag_still_drops():
    html = "<div>" * 20 + "<script>ZZJSZZ</script>" + "<p>keep</p>" + "</div>" * 20
    text = strip_html(html, DEFAULT)
    assert "ZZJSZZ" not in text
    assert "keep" in text


def test_many_stray_close_tags_do_not_desynchronize():
    html = "<p>a</p>" + "</div>" * 2000 + "<p>b</p>"
    text = strip_html(html, DEFAULT)
    assert "a" in text and "b" in text


# ---------------------------------------------------------------------------
# Determinism (R3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_output_is_identical_over_100_runs(name):
    raw = fixture(name)
    first = strip_html(raw, DEFAULT)
    for _ in range(100):
        assert strip_html(raw, DEFAULT) == first


def test_fresh_policy_objects_give_identical_output():
    raw = fixture("volatile_a.html")
    assert strip_html(raw, Policy.default()) == strip_html(raw, Policy.default())
    assert strip_html(raw, Policy.strict()) == strip_html(raw, Policy.strict())


def test_attribute_values_never_reach_the_output():
    """The structural guarantee that makes a volatile-attribute blocklist moot."""
    html = (
        '<div id="ZZIDZZ" class="ZZCLASSZZ" nonce="ZZNONCEZZ" '
        'data-request-id="ZZREQZZ" style="color:ZZSTYLEZZ" title="ZZTITLEZZ">'
        "visible"
        "</div>"
        '<input type="hidden" name="csrf" value="ZZCSRFZZ">'
        '<img src="ZZSRCZZ" alt="ZZALTZZ">'
        '<a href="ZZHREFZZ">link</a>'
    )
    text = strip_html(html, DEFAULT)
    assert text == "visible\nlink" or text == "visible\n\nlink"
    for token in (
        "ZZIDZZ",
        "ZZCLASSZZ",
        "ZZNONCEZZ",
        "ZZREQZZ",
        "ZZSTYLEZZ",
        "ZZTITLEZZ",
        "ZZCSRFZZ",
        "ZZSRCZZ",
        "ZZALTZZ",
        "ZZHREFZZ",
    ):
        assert token not in text, token


def test_stripper_does_not_do_m3_canonicalization():
    """Case, unicode form and numbers are canonicalizer concerns, not ours.

    ``Policy.strict()`` sets ``lowercase=True`` and number banding; if any of
    that leaked into ``strip_html`` the module boundary would be broken and M3
    would be applying it twice.
    """
    html = "<p>Price 1234.5678 ＡＢＣ</p>"
    text = strip_html(html, STRICT)
    assert text == "Price 1234.5678 ＡＢＣ"
