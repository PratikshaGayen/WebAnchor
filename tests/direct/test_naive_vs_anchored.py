"""The proof: NaiveWebReader diverges, AnchoredWebReader converges.

Every test here uses ``direct_vm.mock_web`` to simulate what an independent
validator fetch looks like: mock response A represents the leader's fetch,
mock response B represents a validator's fetch of "the same" page moments
later. ``tests/fixtures/volatile_a.html`` and ``volatile_b.html`` are
hand-verified to differ only in nonce/CSRF/timestamp/ad-rotation/build-hash --
exactly the kind of divergence real pages exhibit between two independent
HTTP requests.

Procedure per contract (mirroring what this project calls "simulated
validators"): deploy the contract once, configure mock A, call the write
method, read back the stored result; then ``direct_vm.clear_mocks()``,
configure mock B, call the SAME write method again on the SAME deployed
instance, read back that result. Compare the two stored results.

A second full *redeployment* of the same contract class within one process
was tried first and rejected: the real GenLayer SDK's
``genlayer.gl.genvm_contracts.Contract.__init_subclass__`` enforces "only
one Contract subclass is allowed per module" as a *process-global*, not
per-module-instance, check (``__known_contract__`` is a module-level
singleton in ``genlayer.gl.genvm_contracts`` that is set once and never
reset). Defining ``AnchoredWebReader`` a second time in the same process --
which is exactly what a second ``direct_deploy`` of the same contract path
does, since the loader re-execs the file as a fresh module -- trips
``TypeError: only one contract is allowed``. This is a real constraint of
the production SDK (one contract per WASM module/execution), not a
direct-mode quirk, so the fix is procedural: reuse one deployment and vary
the mocked response between two writes, which is exactly the "clear_mocks()
+ reconfiguring" alternative described above.

``webanchor.anchor(url, mode="get")`` inside ``AnchoredWebReader`` and
``gl.nondet.web.get(url)`` inside ``NaiveWebReader`` both route through
``gl.nondet.web.get`` under the hood, so the same ``direct_vm.mock_web``
regex/response mocking mechanism drives both contracts identically -- the
only thing that differs between the two contracts is what happens to the
bytes after they're fetched.
"""

import os

import pytest

import webanchor

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")
CORPUS = os.path.join(FIXTURES, "corpus")

TEST_URL = "https://example.com/orders/100418"
URL_PATTERN = r"example\.com/orders/100418"


def _read_fixture(*parts: str) -> str:
    with open(os.path.join(FIXTURES, *parts), "r", encoding="utf-8") as handle:
        return handle.read()


VOLATILE_A = _read_fixture("volatile_a.html")
VOLATILE_B = _read_fixture("volatile_b.html")
NEWS_ARTICLE = _read_fixture("corpus", "news_article.html")
PRODUCT_PAGE = _read_fixture("corpus", "product_page.html")


# ---------------------------------------------------------------------------
# 1. The failure, demonstrated: NaiveWebReader diverges
# ---------------------------------------------------------------------------


def test_naive_reader_diverges_across_simulated_validator_fetches(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    contract = direct_deploy("contracts/naive_reader.py")

    # "Leader" fetch: mock A.
    direct_vm.mock_web(URL_PATTERN, {"status": 200, "body": VOLATILE_A})
    contract.read_page(TEST_URL)
    reading_a = contract.get_last_reading()

    # "Validator" fetch, moments later: mock B on the same deployed contract.
    direct_vm.clear_mocks()
    direct_vm.mock_web(URL_PATTERN, {"status": 200, "body": VOLATILE_B})
    contract.read_page(TEST_URL)
    reading_b = contract.get_last_reading()

    # This is the failure this whole project exists to fix: raw bytes from
    # two independent fetches of "the same" page do not match, so
    # gl.eq_principle.strict_eq would have nothing stable to agree on.
    assert reading_a != reading_b
    assert reading_a == VOLATILE_A
    assert reading_b == VOLATILE_B


# ---------------------------------------------------------------------------
# 2. The fix, demonstrated: AnchoredWebReader converges
# ---------------------------------------------------------------------------


def test_anchored_reader_converges_across_simulated_validator_fetches(
    direct_vm, direct_alice, deploy_anchored
):
    direct_vm.sender = direct_alice
    contract, mod = deploy_anchored()

    # Sanity: the deployment actually resolved to the VENDORED package under
    # contracts/anchored_reader/webanchor/, not the top-level one.
    vendored_dir = os.path.join(REPO_ROOT, "contracts", "anchored_reader")
    assert mod is not None and mod.__file__.startswith(vendored_dir)

    direct_vm.mock_web(URL_PATTERN, {"status": 200, "body": VOLATILE_A})
    contract.read_page(TEST_URL)
    fingerprint_a = contract.get_last_fingerprint()

    direct_vm.clear_mocks()
    direct_vm.mock_web(URL_PATTERN, {"status": 200, "body": VOLATILE_B})
    contract.read_page(TEST_URL)
    fingerprint_b = contract.get_last_fingerprint()

    # This is the fix: two independent fetches of "the same" page, once
    # routed through WebAnchor, produce the SAME fingerprint.
    assert fingerprint_a == fingerprint_b
    assert fingerprint_a.startswith("wa1:")


# ---------------------------------------------------------------------------
# 3. Cross-check: the contract isn't doing something different from the
#    library it claims to wrap.
# ---------------------------------------------------------------------------


def test_anchored_reader_fingerprint_matches_library_computed_directly(
    direct_vm, direct_alice, deploy_anchored
):
    direct_vm.sender = direct_alice

    contract, _mod = deploy_anchored()
    direct_vm.mock_web(URL_PATTERN, {"status": 200, "body": VOLATILE_A})
    contract.read_page(TEST_URL)
    contract_fingerprint = contract.get_last_fingerprint()

    # Computed directly by the (real, top-level) library -- outside any
    # contract, no VM, no mocks -- using the same policy the contract uses.
    expected = webanchor.anchor_html(
        VOLATILE_A, url=TEST_URL, policy=webanchor.Policy.default()
    ).fingerprint

    assert contract_fingerprint == expected


# ---------------------------------------------------------------------------
# 4. WebAnchor must not converge on everything: genuinely different pages
#    still diverge.
# ---------------------------------------------------------------------------


def test_anchored_reader_diverges_on_genuinely_different_pages(
    direct_vm, direct_alice, deploy_anchored
):
    direct_vm.sender = direct_alice
    contract, _mod = deploy_anchored()

    direct_vm.mock_web(URL_PATTERN, {"status": 200, "body": NEWS_ARTICLE})
    contract.read_page(TEST_URL)
    fingerprint_news = contract.get_last_fingerprint()

    direct_vm.clear_mocks()
    direct_vm.mock_web(URL_PATTERN, {"status": 200, "body": PRODUCT_PAGE})
    contract.read_page(TEST_URL)
    fingerprint_product = contract.get_last_fingerprint()

    # A library that returned a constant would pass tests 1-3 trivially and
    # be useless. Two genuinely different pages must still diverge.
    assert fingerprint_news != fingerprint_product
