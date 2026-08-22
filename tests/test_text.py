"""Unicode/whitespace canonicalization: the invisible-divergence layer."""

import unicodedata

import pytest

from webanchor import Policy
from webanchor.text import UNICODE_FOLD_MAP, canonicalize_text, collapse_layout

DEFAULT = Policy.default()
LOWER = Policy.default().with_changes(lowercase=True)
NFC = Policy.default().with_changes(unicode_form="NFC")
NO_COLLAPSE = Policy.default().with_changes(collapse_whitespace=False)

#: Deliberately nasty. Every entry is something a real CDN, CMS or templating
#: engine has been observed to emit inconsistently for one rendered page.
NASTY_CORPUS = [
    "",
    "plain ascii text",
    "café latte",  # NBSP after a composed accent
    "café latte",  # the same word, decomposed
    "price: 1 234,56 €",  # narrow NBSP + NBSP thousands
    "a​b",  # zero width space mid-word
    "a‌b‍c⁠d",  # ZWNJ, ZWJ, word joiner
    "﻿leading BOM",
    "trailing BOM﻿",
    "soft­hyphen",
    "en–dash em—dash horizontal―bar",
    "figure‒dash nb‑hyphen minus−sign",
    "curly ‘single’ and “double”",
    "low ‚quotes‛ and „doubles‟",
    "5′ 6″ tall",
    "itʼs a test",
    "guillemets «bonjour»",
    "wide　ideographic em thin",
    "line1\r\nline2\rline3\nline4",
    "\r\n\r\n\r\n",
    "   leading and trailing   ",
    "many\n\n\n\n\nnewlines",
    "tabs\tand\tmore\ttabs",
    "İstanbul",  # dotted capital I: lower() denormalizes it
    "Áccent Áccent",
    "ellipsis… and fiﬁ ligature",
    "½ ⅓ fractions",
    "你好 \U0001f600 emoji and cjk",
    "mixed  ​–“﻿ all at once",
    "᠎ mongolian vowel separator",
    "nextline",
]

ALL_POLICIES = [
    (DEFAULT, "default"),
    (LOWER, "lowercase"),
    (NFC, "nfc"),
    (NO_COLLAPSE, "no-collapse"),
    (Policy.strict(), "strict"),
    (Policy.default().with_changes(unicode_form="NFKD"), "nfkd"),
    (Policy.default().with_changes(unicode_form="NFD"), "nfd"),
]


# ---------------------------------------------------------------------------
# Idempotency -- the property the whole module hinges on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", NASTY_CORPUS, ids=range(len(NASTY_CORPUS)))
@pytest.mark.parametrize("policy,pid", ALL_POLICIES, ids=[p[1] for p in ALL_POLICIES])
def test_canonicalize_is_idempotent(text, policy, pid):
    once = canonicalize_text(text, policy)
    assert canonicalize_text(once, policy) == once


@pytest.mark.parametrize("text", NASTY_CORPUS, ids=range(len(NASTY_CORPUS)))
def test_canonicalize_is_idempotent_to_a_third_application(text):
    once = canonicalize_text(text, DEFAULT)
    twice = canonicalize_text(once, DEFAULT)
    assert canonicalize_text(twice, DEFAULT) == once


@pytest.mark.parametrize("text", NASTY_CORPUS, ids=range(len(NASTY_CORPUS)))
def test_canonicalize_is_deterministic_across_repeated_calls(text):
    first = canonicalize_text(text, DEFAULT)
    for _ in range(50):
        assert canonicalize_text(text, DEFAULT) == first


# ---------------------------------------------------------------------------
# Line endings, done first
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["a\r\nb", "a\rb", "a\nb", "a\r\n\rb".replace("\r\n\r", "\n")]
)
def test_all_line_ending_variants_converge(text):
    assert "\r" not in canonicalize_text(text, DEFAULT)


def test_crlf_cr_and_lf_produce_identical_output():
    assert (
        canonicalize_text("a\r\nb\r\nc", DEFAULT)
        == canonicalize_text("a\rb\rc", DEFAULT)
        == canonicalize_text("a\nb\nc", DEFAULT)
        == "a\nb\nc"
    )


def test_line_endings_are_folded_even_with_collapse_disabled():
    assert "\r" not in canonicalize_text("a\r\nb", NO_COLLAPSE)


# ---------------------------------------------------------------------------
# Invisible characters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "char",
    ["​", "‌", "‍", "⁠", "﻿", "­", "᠎"],
    ids=["zwsp", "zwnj", "zwj", "wj", "bom", "shy", "mvs"],
)
def test_zero_width_characters_are_deleted_not_spaced(char):
    """Deleted, not turned into a space: they sit *inside* words."""
    assert canonicalize_text("wo" + char + "rd", DEFAULT) == "word"


@pytest.mark.parametrize(
    "char",
    [" ", " ", " ", " ", "　", " ", " ", ""],
    ids=["nbsp", "nnbsp", "emsp", "thinsp", "ideosp", "mmsp", "ogham", "nel"],
)
def test_space_variants_become_one_plain_space(char):
    assert canonicalize_text("a" + char + "b", DEFAULT) == "a b"


def test_next_line_does_not_become_a_paragraph_break():
    """U+0085 is horizontal space here; promoting it would invent structure."""
    assert "\n" not in canonicalize_text("ab", DEFAULT)


# ---------------------------------------------------------------------------
# Dashes and quotes -- what NFKC does NOT do
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "char",
    ["‐", "‑", "‒", "–", "—", "―", "−", "－"],
    ids=["hyphen", "nbhyphen", "figdash", "endash", "emdash", "horbar", "minus", "fw"],
)
def test_dash_variants_fold_to_ascii_hyphen(char):
    assert canonicalize_text("a" + char + "b", DEFAULT) == "a-b"


@pytest.mark.parametrize(
    "char", ["‘", "’", "‚", "‛", "′", "ʼ", "´"]
)
def test_single_quote_variants_fold_to_ascii_apostrophe(char):
    assert canonicalize_text("a" + char + "b", DEFAULT) == "a'b"


@pytest.mark.parametrize("char", ["“", "”", "„", "‟", "″"])
def test_double_quote_variants_fold_to_ascii_quote(char):
    assert canonicalize_text("a" + char + "b", DEFAULT) == 'a"b'


def test_guillemets_are_deliberately_preserved():
    """They are real quotation marks in French, not a rendering accident."""
    assert canonicalize_text("«oui»", DEFAULT) == "«oui»"


# ---------------------------------------------------------------------------
# NFKC is necessary but not sufficient -- measured, not assumed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "char",
    ["​", "‌", "‍", "⁠", "﻿", "­", "–", "—",
     "―", "‒", "−", "‘", "’", "“", "”", "′"],
)
def test_nfkc_alone_would_leave_these_untouched(char):
    """Pins the premise of the module: these need explicit handling.

    If a future Unicode version starts folding one of these, this test fails
    and the fold table can be revisited -- deliberately, with a
    BEHAVIOR_VERSION bump, rather than by silent drift.
    """
    assert unicodedata.normalize("NFKC", char) == char
    assert canonicalize_text("a" + char + "b", DEFAULT) != "a" + char + "b"


def test_nfkc_does_expand_double_prime_and_we_handle_the_result():
    """NFKC turns U+2033 into two U+2032; folding before normalizing avoids it."""
    assert unicodedata.normalize("NFKC", "″") == "′′"
    assert canonicalize_text("6″", DEFAULT) == '6"'


def test_folds_apply_even_under_a_non_nfkc_form():
    """NFC would fold none of the space variants; the table still does."""
    assert unicodedata.normalize("NFC", " ") == " "
    assert canonicalize_text("a b", NFC) == "a b"
    assert canonicalize_text("a—b", NFC) == "a-b"


def test_unicode_form_is_honored():
    composed = canonicalize_text("café", DEFAULT)
    decomposed = canonicalize_text("café", NFC.with_changes(unicode_form="NFD"))
    assert composed == "café"
    assert decomposed == "café"
    assert composed != decomposed


def test_composed_and_decomposed_converge_under_one_form():
    """Built from escapes so the two inputs provably differ in the source."""
    composed = "café"
    decomposed = "café"
    assert composed != decomposed
    assert canonicalize_text(composed, DEFAULT) == canonicalize_text(
        decomposed, DEFAULT
    )


# ---------------------------------------------------------------------------
# Case folding
# ---------------------------------------------------------------------------


def test_lowercase_is_applied_only_when_policy_says_so():
    assert canonicalize_text("MiXeD", DEFAULT) == "MiXeD"
    assert canonicalize_text("MiXeD", LOWER) == "mixed"


def test_lowercasing_a_denormalizing_character_stays_idempotent():
    """U+0130 lowercases to i + U+0307; re-normalizing after lower() is why."""
    once = canonicalize_text("İstanbul", LOWER)
    assert canonicalize_text(once, LOWER) == once
    assert once == unicodedata.normalize("NFKC", once)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def test_horizontal_runs_collapse_and_lines_are_stripped():
    assert canonicalize_text("  a   b  \n   c   ", DEFAULT) == "a b\nc"


def test_blank_line_runs_squeeze_to_exactly_one_blank_line():
    assert canonicalize_text("a\n\n\n\n\nb", DEFAULT) == "a\n\nb"
    assert canonicalize_text("a\n\nb", DEFAULT) == "a\n\nb"


def test_collapse_can_be_disabled_but_lines_are_still_stripped():
    assert canonicalize_text("a  \n  b", NO_COLLAPSE) == "a\nb"
    assert canonicalize_text("a  b", NO_COLLAPSE) == "a  b"


def test_empty_and_whitespace_only_inputs_return_empty():
    for text in ["", "   ", "\n\n\n", "\r\n", " ​"]:
        assert canonicalize_text(text, DEFAULT) == ""


def test_collapse_layout_is_idempotent():
    for text in NASTY_CORPUS:
        once = collapse_layout(text, True)
        assert collapse_layout(once, True) == once


# ---------------------------------------------------------------------------
# The fold table itself
# ---------------------------------------------------------------------------


def test_every_fold_target_is_ascii_or_deletion():
    """This is *why* applying the table twice is a no-op."""
    for value in UNICODE_FOLD_MAP.values():
        assert value is None or (len(value) == 1 and ord(value) < 128)


def test_no_fold_key_is_ascii():
    """Folding an ASCII character would make the table non-idempotent."""
    for key in UNICODE_FOLD_MAP:
        assert key >= 128


def test_fold_map_has_no_duplicate_classification():
    assert len(UNICODE_FOLD_MAP) == len(set(UNICODE_FOLD_MAP))


def test_two_renderings_of_the_same_page_converge():
    """The end-to-end claim, in miniature."""
    render_a = "Price : 1 234,56 — “best” deal​!"
    render_b = "Price : 1 234,56 - \"best\" deal!"
    assert canonicalize_text(render_a, DEFAULT) == canonicalize_text(
        render_b, DEFAULT
    )
