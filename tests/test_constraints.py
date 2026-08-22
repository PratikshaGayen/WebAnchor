"""Structural enforcement of blueprint rules R1 (stdlib only) and R2/R3.

These tests parse the package with ``ast`` rather than trusting review: a
third-party import, a top-level ``genlayer`` import, or a salted ``hash()``
call would each break deployment or consensus in ways unit tests of behavior
would not catch.
"""

import ast
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_DIR = os.path.join(REPO_ROOT, "webanchor")

ALLOWED_MODULES = {
    "re",
    "html",
    "hashlib",
    "hmac",
    "json",
    "unicodedata",
    "datetime",
    "decimal",
    "dataclasses",
    "typing",
    "enum",
    "sys",
    "math",
    "collections",
    "functools",
    "itertools",
    "webanchor",
}

# genlayer bindings are permitted only inside fetch.py, and only lazily
# (see test_no_module_level_genlayer_import).
GENLAYER_MODULES = {"genlayer", "gl"}
GENLAYER_ONLY_FILE = "fetch.py"


def _py_files():
    found = []
    for dirpath, dirnames, filenames in os.walk(PKG_DIR):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if name.endswith(".py"):
                found.append(os.path.join(dirpath, name))
    return found


def _parse(path):
    with open(path, "r", encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=path)


PY_FILES = _py_files()


def test_package_has_source_files():
    assert PY_FILES, "no python files found under webanchor/"


def _imported_roots(tree):
    """Yield (root_module, node) for every import in the tree."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                yield "webanchor", node
            elif node.module:
                yield node.module.split(".")[0], node


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: os.path.basename(p))
def test_only_allowlisted_modules_are_imported(path):
    tree = _parse(path)
    for root, node in _imported_roots(tree):
        if root in GENLAYER_MODULES:
            assert os.path.basename(path) == GENLAYER_ONLY_FILE, (
                "R2 violation: {0} line {1} imports {2!r}; only {3} may touch "
                "GenLayer".format(path, node.lineno, root, GENLAYER_ONLY_FILE)
            )
            continue
        assert root in ALLOWED_MODULES, (
            "R1 violation: {0} line {1} imports {2!r}, which is not in the "
            "stdlib allowlist {3}".format(
                path, node.lineno, root, sorted(ALLOWED_MODULES)
            )
        )


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: os.path.basename(p))
def test_no_module_level_genlayer_import(path):
    tree = _parse(path)
    for node in tree.body:
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split(".")[0]]
        for name in names:
            assert name not in GENLAYER_MODULES, (
                "R2 violation: {0} line {1} imports {2!r} at module scope; it "
                "must be imported lazily inside a function body".format(
                    path, node.lineno, name
                )
            )


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: os.path.basename(p))
def test_no_builtin_hash_call(path):
    tree = _parse(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "hash":
                pytest.fail(
                    "R3 violation: {0} line {1} calls builtin hash(), which is "
                    "salted by PYTHONHASHSEED".format(path, node.lineno)
                )


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: os.path.basename(p))
def test_no_random_and_no_wall_clock(path):
    tree = _parse(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "random":
            pytest.fail(
                "R3 violation: {0} line {1} references random".format(
                    path, node.lineno
                )
            )
        if isinstance(node, ast.Attribute) and node.attr in ("time", "time_ns"):
            value = node.value
            if isinstance(value, ast.Name) and value.id == "time":
                pytest.fail(
                    "R3 violation: {0} line {1} uses wall-clock time.{2}()".format(
                        path, node.lineno, node.attr
                    )
                )
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raw = ast.dump(node)
            assert "'random'" not in raw, "R3 violation: {0} imports random".format(
                path
            )


def test_import_works_without_genlayer():
    import webanchor

    assert webanchor.__version__ == "0.1.0"
    assert "genlayer" not in sys.modules
    with pytest.raises(ImportError):
        __import__("genlayer")


def test_clean_subprocess_import_of_public_api():
    script = (
        "import sys, webanchor;"
        "assert 'genlayer' not in sys.modules;"
        "[getattr(webanchor, n) for n in webanchor.__all__];"
        "print('ok')"
    )
    env = {"PYTHONPATH": REPO_ROOT, "PYTHONIOENCODING": "utf-8"}
    for key in ("PATH", "SYSTEMROOT", "SystemRoot", "COMSPEC", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


def test_public_api_names_match_blueprint():
    import webanchor

    for name in (
        "Policy",
        "Evidence",
        "anchor",
        "anchor_html",
        "fingerprint",
        "verify",
        "WebAnchorError",
        "ERROR_BY_CODE",
        "from_status",
    ):
        assert name in webanchor.__all__
        assert hasattr(webanchor, name)


def test_fetch_module_is_not_imported_by_the_core():
    """R2: ``import webanchor`` must not pull in the GenLayer boundary module.

    Checked in a CLEAN SUBPROCESS rather than in this one.  An in-process
    assertion here is order-dependent and therefore unsound: any earlier test
    that legitimately imports ``webanchor.fetch`` -- ``tests/test_fetch.py``
    does, and must -- binds it as an attribute of the package, and this test
    would then fail for a reason that has nothing to do with the property it
    is guarding.  Worse, in the other test order it would pass while telling
    you nothing.  A fresh interpreter is the only place the question "does
    importing the package import fetch?" actually has an answer.
    """
    script = (
        "import sys, webanchor;"
        "assert not hasattr(webanchor, 'fetch'), 'webanchor.fetch was bound';"
        "assert 'webanchor.fetch' not in sys.modules, 'fetch was imported';"
        "assert 'genlayer' not in sys.modules;"
        "print('ok')"
    )
    env = {"PYTHONPATH": REPO_ROOT, "PYTHONIOENCODING": "utf-8"}
    for key in ("PATH", "SYSTEMROOT", "SystemRoot", "COMSPEC", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


def test_importing_fetch_directly_still_needs_no_genlayer():
    """The boundary module itself must import cleanly off-chain (R2).

    Only *calling* into it may fail.  If importing it required the SDK, the
    lazy-import discipline inside the function bodies would be pointless.
    """
    script = (
        "import sys, webanchor.fetch;"
        "assert 'genlayer' not in sys.modules;"
        "print('ok')"
    )
    env = {"PYTHONPATH": REPO_ROOT, "PYTHONIOENCODING": "utf-8"}
    for key in ("PATH", "SYSTEMROOT", "SystemRoot", "COMSPEC", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"
