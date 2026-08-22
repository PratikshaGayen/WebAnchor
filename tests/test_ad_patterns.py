"""``ad_container_patterns`` replaces; ``extra_ad_container_patterns`` unions.

The footgun this closes: a user who wants the ten built-in patterns *plus*
``housead`` sets ``ad_container_patterns=("housead",)`` and silently loses all
ten.  Silent loss of a defence is the same class of failure as silent loss of
content -- nothing in the output says the policy is now weaker than intended.
"""

import pytest

from webanchor import Policy
from webanchor.html_strip import strip_html

HTML = (
    "<body>"
    "<p>Editorial content.</p>"
    '<div class="ad-slot">ZZADZZ</div>'
    '<div class="housead">ZZHOUSEZZ</div>'
    '<div class="takeover">ZZTAKEOVERZZ</div>'
    '<div class="download">ZZDOWNLOADZZ</div>'
    "</body>"
)


def strip_with(**kwargs):
    return strip_html(HTML, Policy(strip_ad_containers=True, **kwargs))


# ---------------------------------------------------------------------------
# The effective set
# ---------------------------------------------------------------------------


def test_default_effective_set_is_the_builtin_list():
    policy = Policy.default()
    assert policy.extra_ad_container_patterns == ()
    assert set(policy.effective_ad_container_patterns()) == set(
        policy.ad_container_patterns
    )


def test_effective_set_is_the_union():
    policy = Policy(
        ad_container_patterns=("ad", "promo"),
        extra_ad_container_patterns=("housead", "takeover"),
    )
    assert policy.effective_ad_container_patterns() == (
        "ad",
        "housead",
        "promo",
        "takeover",
    )


def test_effective_set_is_sorted_not_raw_set_order():
    """R3: never iterate a raw set into anything that affects output."""
    policy = Policy(
        ad_container_patterns=("zeta", "alpha"),
        extra_ad_container_patterns=("mu", "beta"),
    )
    effective = policy.effective_ad_container_patterns()
    assert effective == tuple(sorted(effective))
    assert isinstance(effective, tuple)


def test_effective_set_deduplicates():
    policy = Policy(
        ad_container_patterns=("ad", "promo"),
        extra_ad_container_patterns=("promo", "ad", "extra"),
    )
    assert policy.effective_ad_container_patterns() == ("ad", "extra", "promo")


def test_effective_set_is_stable_across_calls_and_equal_policies():
    a = Policy(ad_container_patterns=("b", "a"), extra_ad_container_patterns=("c",))
    b = Policy(ad_container_patterns=("a", "b"), extra_ad_container_patterns=("c",))
    assert a.effective_ad_container_patterns() == a.effective_ad_container_patterns()
    assert a.effective_ad_container_patterns() == b.effective_ad_container_patterns()


def test_declaration_order_does_not_change_the_effective_set():
    a = Policy(extra_ad_container_patterns=("zzz", "aaa"))
    b = Policy(extra_ad_container_patterns=("aaa", "zzz"))
    assert a.effective_ad_container_patterns() == b.effective_ad_container_patterns()


def test_a_list_argument_is_normalized_to_a_tuple():
    policy = Policy(extra_ad_container_patterns=["one", "two"])
    assert policy.extra_ad_container_patterns == ("one", "two")


# ---------------------------------------------------------------------------
# policy_id
# ---------------------------------------------------------------------------


def test_extra_patterns_reach_policy_id():
    base = Policy.default()
    assert base.with_changes(
        extra_ad_container_patterns=("housead",)
    ).policy_id != base.policy_id


def test_the_two_fields_are_independent_in_policy_id():
    a = Policy(ad_container_patterns=("ad", "housead"))
    b = Policy(ad_container_patterns=("ad",), extra_ad_container_patterns=("housead",))
    # Same effective set, different declarations -> different ids.  Erring
    # toward *visible* difference is correct: two policies that were written
    # differently should be distinguishable in a consensus failure report.
    assert a.effective_ad_container_patterns() == b.effective_ad_container_patterns()
    assert a.policy_id != b.policy_id


def test_effective_set_is_serialized_for_auditability():
    import json

    data = json.loads(Policy(extra_ad_container_patterns=("zz",)).canonical_json())
    assert "ad_container_patterns" in data
    assert "extra_ad_container_patterns" in data
    assert data["effective_ad_container_patterns"] == sorted(
        set(data["ad_container_patterns"]) | set(data["extra_ad_container_patterns"])
    )


# ---------------------------------------------------------------------------
# Behavior: each field alone, and both together
# ---------------------------------------------------------------------------


def test_replacing_field_alone_drops_the_defaults():
    """Documented, and this is exactly why the extra field exists."""
    text = strip_with(ad_container_patterns=("housead",))
    assert "ZZHOUSEZZ" not in text
    assert "ZZADZZ" in text, "the built-in 'ad' pattern is gone, as documented"
    assert "Editorial content." in text


def test_extra_field_alone_keeps_the_defaults_and_adds_to_them():
    text = strip_with(extra_ad_container_patterns=("housead",))
    assert "ZZADZZ" not in text, "built-in 'ad' pattern must still apply"
    assert "ZZHOUSEZZ" not in text, "the added pattern must apply too"
    assert "ZZTAKEOVERZZ" in text
    assert "Editorial content." in text


def test_both_fields_together_union_their_effects():
    text = strip_with(
        ad_container_patterns=("ad",), extra_ad_container_patterns=("takeover",)
    )
    assert "ZZADZZ" not in text
    assert "ZZTAKEOVERZZ" not in text
    assert "ZZHOUSEZZ" in text
    assert "Editorial content." in text


def test_neither_field_matters_when_stripping_is_off():
    """Conservative by default stays conservative."""
    policy = Policy(extra_ad_container_patterns=("housead", "takeover"))
    assert policy.strip_ad_containers is False
    text = strip_html(HTML, policy)
    for marker in ("ZZADZZ", "ZZHOUSEZZ", "ZZTAKEOVERZZ", "ZZDOWNLOADZZ"):
        assert marker in text


def test_extra_patterns_still_match_whole_tokens_only():
    """Substring matching would eat ``class="download"``; it must not."""
    text = strip_with(extra_ad_container_patterns=("load",))
    assert "ZZDOWNLOADZZ" in text, "'download' is not the token 'load'"


def test_extra_patterns_are_case_insensitive():
    html = '<body><p>keep</p><div class="HouseAd">ZZHOUSEZZ</div></body>'
    text = strip_html(
        html, Policy(strip_ad_containers=True, extra_ad_container_patterns=("housead",))
    )
    assert "ZZHOUSEZZ" not in text
    assert "keep" in text


@pytest.mark.parametrize("attribute", ["class", "id"])
def test_extra_patterns_match_class_and_id_alike(attribute):
    html = '<body><p>keep</p><div {0}="housead">ZZHOUSEZZ</div></body>'.format(
        attribute
    )
    text = strip_html(
        html, Policy(strip_ad_containers=True, extra_ad_container_patterns=("housead",))
    )
    assert "ZZHOUSEZZ" not in text


def test_empty_extra_patterns_change_nothing():
    a = strip_html(HTML, Policy.strict())
    b = strip_html(HTML, Policy.strict().with_changes(extra_ad_container_patterns=()))
    assert a == b


def test_stripping_output_is_deterministic_with_many_extra_patterns():
    """A set iterated in hash order would make this flaky across processes."""
    policy = Policy(
        strip_ad_containers=True,
        extra_ad_container_patterns=tuple("pat{0}".format(i) for i in range(50)),
    )
    first = strip_html(HTML, policy)
    for _ in range(50):
        assert strip_html(HTML, policy) == first
