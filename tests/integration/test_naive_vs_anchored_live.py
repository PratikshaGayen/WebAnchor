"""The proof, for real: NaiveWebReader diverges under real consensus,
AnchoredWebReader converges, against a real GenLayer Studio network.

This is the Studio-mode (live network) counterpart to
``tests/direct/test_naive_vs_anchored.py``. Direct mode runs only the
leader function, in-process, with no real consensus -- it is a fast,
deterministic proxy for the real thing. This module is the real thing: it
deploys both contracts to the live GenLayer Studio stack running in Docker
(three independent consensus-worker containers, each running its own leader
or validator GenVM process against its own web module), and calls
``read_page`` against a real HTTP server that returns genuinely different
bytes on every single request (see ``volatile_server.py``).

Run with:

    gltest tests/integration/ -v -s

Requires:
  * The GenLayer Studio Docker stack running (``docker ps`` should show
    ``genlayer-studio-jsonrpc-1`` and three ``genlayer-studio-consensus-worker``
    containers healthy).
  * Ability to bind port 80 on this host (see conftest.py) -- the fixture
    server must use port 80 because GenVM's web module only allows ports
    80/443.
  * Each consensus-worker's web module must have ``host.docker.internal`` in
    its ``always_allow_hosts`` config (see conftest.py docstring for how this
    was set up against the already-running containers).

Why not ``mock_web_response``: that mechanism matches one fixed response to
one fixed URL pattern for every validator, so every validator would see
IDENTICAL bytes -- exactly the case ``strict_eq`` handles trivially, and not
a demonstration of the real problem WebAnchor solves. This module makes real,
independent HTTP requests instead: the leader and every validator each hit
``volatile_url`` over a real socket at a different moment and get different
bytes back, because the fixture server generates a fresh nonce, CSRF token,
timestamp, ad id, and viewer count on every single request.
"""

from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded


# ---------------------------------------------------------------------------
# 1. The failure, demonstrated for real: NaiveWebReader's consensus fails.
# ---------------------------------------------------------------------------


def test_naive_reader_consensus_fails_on_volatile_content(volatile_url):
    factory = get_contract_factory(contract_file_path="naive_reader.py")
    contract = factory.deploy(args=[])

    # NOTE: gltest's tx_execution_succeeded() only checks whether the
    # LEADER's own execution raised a Python exception -- it does NOT check
    # whether consensus (the majority vote across validators) was reached.
    # A validator that independently fetches different bytes than the
    # leader does not raise; it just returns a different value, which the
    # host then compares and votes "disagree" on. So the real signal for
    # "consensus failed because the fetches diverged" is the transaction's
    # own `status_name` / `result_name`, not tx_execution_succeeded(). This
    # was confirmed against the live network before writing this assertion
    # (see the integration report for the raw CLI output showing
    # `result_name: 'MAJORITY_DISAGREE'`, `status_name: 'UNDETERMINED'` for
    # exactly this call).
    receipt = contract.read_page(args=[volatile_url]).transact()

    assert receipt["status_name"] == "UNDETERMINED", (
        "Expected real independent validator fetches of genuinely volatile "
        f"content to fail to reach agreement (status_name='UNDETERMINED'), "
        f"got status_name={receipt.get('status_name')!r}, "
        f"result_name={receipt.get('result_name')!r}. Full receipt: {receipt}"
    )
    assert receipt["result_name"] == "MAJORITY_DISAGREE", (
        f"Expected result_name='MAJORITY_DISAGREE' (raw bytes never match "
        f"across independent fetches of volatile content), got "
        f"{receipt.get('result_name')!r}. Full receipt: {receipt}"
    )


# ---------------------------------------------------------------------------
# 2. The fix, demonstrated for real: AnchoredWebReader's consensus succeeds.
# ---------------------------------------------------------------------------


def test_anchored_reader_consensus_succeeds_on_volatile_content(volatile_url):
    factory = get_contract_factory(
        contract_file_path="anchored_reader_multi/__init__.py"
    )
    contract = factory.deploy(args=[])

    receipt = contract.read_page(args=[volatile_url]).transact()

    assert tx_execution_succeeded(receipt), (
        f"Expected the leader's own execution to succeed. Full receipt: {receipt}"
    )
    assert receipt["status_name"] == "ACCEPTED", (
        "Expected real consensus to be reached (status_name='ACCEPTED') "
        "even though every validator fetched genuinely different raw bytes, "
        f"because WebAnchor's normalization converges. Got "
        f"status_name={receipt.get('status_name')!r}, "
        f"result_name={receipt.get('result_name')!r}. Full receipt: {receipt}"
    )
    assert receipt["result_name"] == "MAJORITY_AGREE", (
        f"Expected result_name='MAJORITY_AGREE', got "
        f"{receipt.get('result_name')!r}. Full receipt: {receipt}"
    )

    # 3. Read back the stored fingerprint via the view method and confirm
    #    it is a real, non-empty value in WebAnchor's fingerprint format.
    fingerprint = contract.get_last_fingerprint(args=[]).call()
    assert isinstance(fingerprint, str) and fingerprint.startswith("wa1:"), (
        f"Expected a non-empty 'wa1:<hex>' fingerprint, got {fingerprint!r}"
    )
    assert len(fingerprint) > len("wa1:")

    stored_url = contract.get_last_url(args=[]).call()
    assert stored_url == volatile_url

    policy_id = contract.get_last_policy_id(args=[]).call()
    assert isinstance(policy_id, str) and policy_id.startswith("p1:")
