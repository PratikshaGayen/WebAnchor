"""R3 proof: identical output across processes with different hash seeds.

Python salts ``hash()`` of str/bytes per process via ``PYTHONHASHSEED``.  If any
identity in WebAnchor ever leaks that salt, validators in separate processes
produce different fingerprints and consensus silently breaks.  This test runs
the same script under two different seeds and demands byte-identical output.
"""

import os
import subprocess
import sys

from webanchor import Policy, fingerprint
from webanchor.pipeline import normalize
from webanchor.text import canonicalize_text

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "volatile_a.html"
).replace("\\", "/")

FIXTURE_B = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "volatile_b.html"
).replace("\\", "/")

SCRIPT = r"""
import io
import webanchor
from webanchor import Policy, fingerprint
from webanchor.html_strip import strip_html
from webanchor.pipeline import normalize
from webanchor.text import canonicalize_text

text = "Prix: 1 234,56€ — café 你好 \U0001f600 stock: 42"
default_policy = Policy.default()
strict_policy = Policy.strict()

print(webanchor.__version__)
print(default_policy.policy_id)
print(strict_policy.policy_id)
print(default_policy.canonical_json())
print(fingerprint(text, default_policy.policy_id))
print(fingerprint(text, strict_policy.policy_id))
print(sorted(webanchor.ERROR_BY_CODE))

# The stripper must be stable across processes too, not just in-process.
raw = io.open("__FIXTURE__", encoding="utf-8").read()
stripped = strip_html(raw, default_policy)
print(repr(stripped))
print(fingerprint(stripped, default_policy.policy_id))
print(repr(strip_html(raw, strict_policy)))

# The full normalization path, under both policies.  Every stage is
# a fresh chance to leak process state -- a set iterated in hash order, a
# float repr, a locale-dependent strftime -- so the composed output is what
# has to be compared, not just the stripper's.
for policy in (default_policy, strict_policy):
    normalized, bands = normalize(raw, policy)
    print(repr(normalized))
    print(repr(sorted(bands.items())))
    print(fingerprint(normalized, policy.policy_id))

# The canonicalizer over a corpus that is nothing but divergence
# sources -- invisible characters, dash and quote variants, mixed line
# endings, decomposed accents, non-Latin scripts, astral-plane emoji.
nasty = (
    "caf\u00e9\u00a0latte \u2014 \u201cbest\u201d \u2018deal\u2019 "
    "1\u202f234,56 a\u200bb\ufeff \u00ad soft \u3000wide "
    "cafe\u0301 \u0130stanbul \u4f60\u597d \U0001f600 5\u2032 6\u2033"
)
for policy in (default_policy, strict_policy):
    canon = canonicalize_text(nasty, policy)
    print(repr(canon))
    print(repr(canonicalize_text(canon, policy)))

# Timestamp and number handling, including the quantize mode that has
# to survive the number stage intact.
volatile = (
    "Posted 2024-04-12T08:15:22Z, updated 4 minutes ago, "
    "Fri, 12 Apr 2024 08:15:22 GMT, price $1,234.56 / 1.234,56 / 12.5%"
)
for policy in (default_policy, strict_policy,
               strict_policy.with_changes(timestamp_mode="quantize"),
               default_policy.with_changes(number_band_mode="grid",
                                           number_grid_step="100")):
    print(policy.policy_id)
    print(repr(normalize("<p>" + volatile + "</p>", policy)))

# The top-level API.  ``anchor_html`` adds detection, fingerprinting and
# an Evidence on top of ``normalize``; each of those is a fresh chance to leak
# process state, and the Evidence is what actually reaches consensus calldata,
# so the calldata dict -- not just the text -- is what has to be compared.
raw_b = io.open("__FIXTURE_B__", encoding="utf-8").read()
for policy in (default_policy, strict_policy):
    ev_a = webanchor.anchor_html(raw, "https://example.test/p", policy)
    ev_b = webanchor.anchor_html(raw_b, "https://example.test/p", policy)
    print(repr(sorted(ev_a.to_calldata().items())))
    print(repr(sorted(ev_b.to_calldata().items())))
    # The money claim, re-asserted inside the subprocess: two captures of one
    # page converge here too, not only in the parent interpreter.
    print(ev_a.fingerprint == ev_b.fingerprint)
""".replace("__FIXTURE__", FIXTURE).replace("__FIXTURE_B__", FIXTURE_B)


def _run(seed):
    env = {
        "PYTHONHASHSEED": seed,
        "PYTHONPATH": REPO_ROOT,
        "PYTHONIOENCODING": "utf-8",
    }
    for key in ("PATH", "SYSTEMROOT", "SystemRoot", "COMSPEC", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_identical_output_across_hash_seeds():
    a = _run("0")
    b = _run("12345")
    assert a.strip() != ""
    assert a == b, "output diverged between PYTHONHASHSEED=0 and 12345"


def test_subprocess_output_matches_in_process_values():
    from webanchor import Policy, fingerprint

    lines = _run("0").strip().splitlines()
    assert lines[1] == Policy.default().policy_id
    assert lines[2] == Policy.strict().policy_id
    text = "Prix: 1 234,56€ — café 你好 \U0001f600 stock: 42"
    assert lines[4] == fingerprint(text, Policy.default().policy_id)


def test_subprocess_strip_html_matches_in_process():
    """The stripper is stable across processes, not just calls."""
    import io as _io

    from webanchor import Policy, fingerprint
    from webanchor.html_strip import strip_html

    raw = _io.open(FIXTURE, encoding="utf-8").read()
    stripped = strip_html(raw, Policy.default())
    lines = _run("0").strip().splitlines()
    assert lines[7] == repr(stripped)
    assert lines[8] == fingerprint(stripped, Policy.default().policy_id)
    assert lines[9] == repr(strip_html(raw, Policy.strict()))


def test_a_third_seed_also_agrees():
    assert _run("0") == _run("987654321")


def _lines():
    return _run("0").strip().splitlines()


def test_subprocess_normalize_matches_in_process():
    """The composed pipeline is stable across processes.

    ``strip_html`` being deterministic does not make ``normalize``
    deterministic -- each added stage introduces its own opportunities to leak
    process state (set iteration order, float repr, platform ``strftime``).
    """
    import io as _io

    raw = _io.open(FIXTURE, encoding="utf-8").read()
    lines = _lines()
    offset = 10
    for policy in (Policy.default(), Policy.strict()):
        text, bands = normalize(raw, policy)
        assert lines[offset] == repr(text)
        assert lines[offset + 1] == repr(sorted(bands.items()))
        assert lines[offset + 2] == fingerprint(text, policy.policy_id)
        offset += 3


def test_subprocess_canonicalize_matches_in_process():
    nasty = (
        "caf\u00e9\u00a0latte \u2014 \u201cbest\u201d \u2018deal\u2019 "
        "1\u202f234,56 a\u200bb\ufeff \u00ad soft \u3000wide "
        "cafe\u0301 \u0130stanbul \u4f60\u597d \U0001f600 5\u2032 6\u2033"
    )
    lines = _lines()
    offset = 16
    for policy in (Policy.default(), Policy.strict()):
        canon = canonicalize_text(nasty, policy)
        assert lines[offset] == repr(canon)
        # ...and idempotent in the subprocess too, not only in this one.
        assert lines[offset + 1] == repr(canon)
        offset += 2


def test_subprocess_timestamp_and_number_handling_matches_in_process():
    volatile = (
        "Posted 2024-04-12T08:15:22Z, updated 4 minutes ago, "
        "Fri, 12 Apr 2024 08:15:22 GMT, price $1,234.56 / 1.234,56 / 12.5%"
    )
    lines = _lines()
    offset = 20
    policies = [
        Policy.default(),
        Policy.strict(),
        Policy.strict().with_changes(timestamp_mode="quantize"),
        Policy.default().with_changes(
            number_band_mode="grid", number_grid_step="100"
        ),
    ]
    for policy in policies:
        assert lines[offset] == policy.policy_id
        assert lines[offset + 1] == repr(
            normalize("<p>" + volatile + "</p>", policy)
        )
        offset += 2


def test_no_wall_clock_leaks_into_the_normalized_output():
    """Two subprocess runs separated in the transcript must still agree.

    If any stage read ``time.time()``, these two runs would differ; the
    fixture and the policies are identical, so anything that differs is state
    the pipeline should not have been reading.
    """
    assert _run("0") == _run("0")


# ---------------------------------------------------------------------------
# anchor_html across processes
# ---------------------------------------------------------------------------


def test_subprocess_anchor_html_matches_in_process():
    """The top-level API is stable across processes, not just the pure core.

    ``normalize`` being deterministic does not make ``anchor_html``
    deterministic: detection, the policy_id, the fingerprint and the calldata
    dict ordering are all additional surfaces, and the calldata dict is the
    thing that actually reaches consensus.
    """
    import io as _io

    from webanchor import anchor_html

    raw_a = _io.open(FIXTURE, encoding="utf-8").read()
    raw_b = _io.open(FIXTURE_B, encoding="utf-8").read()
    lines = _lines()
    offset = 28
    for policy in (Policy.default(), Policy.strict()):
        ev_a = anchor_html(raw_a, "https://example.test/p", policy)
        ev_b = anchor_html(raw_b, "https://example.test/p", policy)
        assert lines[offset] == repr(sorted(ev_a.to_calldata().items()))
        assert lines[offset + 1] == repr(sorted(ev_b.to_calldata().items()))
        assert lines[offset + 2] == "True"
        offset += 3


def test_the_two_captures_converge_in_the_subprocess_too():
    """Convergence is a property of the code, not of one interpreter."""
    lines = _lines()
    assert lines[30] == "True"
    assert lines[33] == "True"
