"""The GenLayer boundary: lazy import, error wrapping, and the compat shim.

``genlayer`` is not installed here and never will be in CI -- that is the
point of blueprint rule R2.  These tests therefore do two things: prove the
absent-SDK path fails in the documented way, and prove the present-SDK path by
installing a fake ``genlayer`` module into ``sys.modules`` for the duration of
one test.  A fake is honest here: ``fetch.py`` contains no logic beyond
"call the SDK, wrap the failure, shape the result", and that is exactly what
the fake exercises.
"""

import ast
import os
import sys
import types

import pytest

from webanchor import fetch as fetch_module
from webanchor.errors import NetworkError, WebAnchorError
from webanchor.fetch import FETCH_MODES, RENDER_MODES, fetch_raw, get_webpage

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FETCH_PATH = os.path.join(REPO_ROOT, "webanchor", "fetch.py")


class _FakeWeb:
    def __init__(self, *, get_result=None, render_result=None, boom=None):
        self._get_result = get_result
        self._render_result = render_result
        self._boom = boom
        self.calls = []

    def get(self, url):
        self.calls.append(("get", url, {}))
        if self._boom is not None:
            raise self._boom
        return self._get_result

    def render(self, url, mode="text", wait_after_loaded=None):
        self.calls.append(
            ("render", url, {"mode": mode, "wait_after_loaded": wait_after_loaded})
        )
        if self._boom is not None:
            raise self._boom
        return self._render_result


class _FakeResponse:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body


def install_fake_gl(monkeypatch, web):
    """Put a minimal fake ``genlayer`` package into ``sys.modules``."""
    gl = types.SimpleNamespace(nondet=types.SimpleNamespace(web=web))
    module = types.ModuleType("genlayer")
    module.gl = gl
    monkeypatch.setitem(sys.modules, "genlayer", module)
    return gl


# ---------------------------------------------------------------------------
# R2, enforced structurally
# ---------------------------------------------------------------------------


def test_genlayer_is_imported_only_inside_function_bodies():
    """A module-scope import here would break ``import webanchor`` everywhere."""
    with open(FETCH_PATH, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=FETCH_PATH)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in ("genlayer", "gl")
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in ("genlayer", "gl")


def test_importing_webanchor_does_not_import_fetch():
    import webanchor  # noqa: F401

    assert "genlayer" not in sys.modules


def test_the_removed_sdk_name_is_never_called_in_code():
    """``gl.get_webpage`` is the pre-v0.1.3 name and no longer exists.

    Checked over the AST rather than the source text: the docstring names the
    removed API on purpose, so a substring search would either fail here or
    force the documentation to stop saying what it needs to say.
    """
    with open(FETCH_PATH, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=FETCH_PATH)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "get_webpage":
            value = node.value
            assert not (isinstance(value, ast.Name) and value.id == "gl"), (
                "fetch.py calls the removed gl.get_webpage at line %d"
                % node.lineno
            )


def test_the_current_sdk_entry_points_are_the_ones_used():
    with open(FETCH_PATH, "r", encoding="utf-8") as handle:
        source = handle.read()
    assert "gl.nondet.web.render(" in source
    assert "gl.nondet.web.get(" in source


# ---------------------------------------------------------------------------
# Absent SDK: a clear, actionable error
# ---------------------------------------------------------------------------


def test_fetch_raw_raises_a_clear_error_with_no_genlayer():
    with pytest.raises(WebAnchorError) as info:
        fetch_raw("https://example.test/")
    detail = info.value.detail
    assert "GenVM" in detail
    assert "anchor_html" in detail, "the error must name the offline alternative"


def test_the_absent_sdk_error_is_not_a_bare_importerror():
    """R4 wants typed failures; ``No module named 'genlayer'`` is not one."""
    with pytest.raises(WebAnchorError) as info:
        fetch_raw("https://example.test/")
    assert not isinstance(info.value, ImportError)


def test_get_webpage_also_raises_without_genlayer():
    with pytest.raises(WebAnchorError):
        get_webpage("https://example.test/")


# ---------------------------------------------------------------------------
# Mode validation happens before the SDK import
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["", "HTML", "render", "fetch", "json"])
def test_an_unknown_mode_is_rejected_and_names_the_valid_ones(mode):
    with pytest.raises(WebAnchorError) as info:
        fetch_raw("https://example.test/", mode=mode)
    assert "unknown fetch mode" in info.value.detail
    for valid in FETCH_MODES:
        assert valid in info.value.detail


def test_mode_validation_precedes_the_import_so_the_message_is_useful():
    """Otherwise a typo off-chain reports a missing dependency instead."""
    with pytest.raises(WebAnchorError) as info:
        fetch_raw("https://example.test/", mode="nope")
    assert "GenVM" not in info.value.detail


@pytest.mark.parametrize("mode", ["html", "text", "get"])
def test_every_documented_mode_passes_validation(mode):
    with pytest.raises(WebAnchorError) as info:
        fetch_raw("https://example.test/", mode=mode)
    assert "unknown fetch mode" not in info.value.detail


def test_mode_tables_are_tuples_and_consistent():
    assert isinstance(FETCH_MODES, tuple)
    assert isinstance(RENDER_MODES, tuple)
    assert set(RENDER_MODES) < set(FETCH_MODES)
    assert "get" not in RENDER_MODES


# ---------------------------------------------------------------------------
# Present (fake) SDK: get mode
# ---------------------------------------------------------------------------


def test_get_mode_returns_status_headers_and_decoded_body(monkeypatch):
    web = _FakeWeb(
        get_result=_FakeResponse(
            200, {"Server": "nginx", "Content-Type": "text/html"}, b"<p>hi</p>"
        )
    )
    install_fake_gl(monkeypatch, web)
    status, headers, text = fetch_raw("https://example.test/", mode="get")
    assert status == 200
    assert headers == {"Server": "nginx", "Content-Type": "text/html"}
    assert text == "<p>hi</p>"
    assert web.calls == [("get", "https://example.test/", {})]


def test_get_mode_preserves_a_non_200_status_for_the_detector(monkeypatch):
    web = _FakeWeb(get_result=_FakeResponse(429, {"Retry-After": "60"}, b"slow down"))
    install_fake_gl(monkeypatch, web)
    status, headers, text = fetch_raw("https://example.test/", mode="get")
    assert status == 429
    assert headers == {"Retry-After": "60"}


def test_get_mode_decodes_utf8_with_replacement_and_does_not_raise(monkeypatch):
    """A mis-encoded byte must not rob ``detect`` of the chance to explain."""
    web = _FakeWeb(get_result=_FakeResponse(200, {}, b"caf\xe9 latte"))
    install_fake_gl(monkeypatch, web)
    _status, _headers, text = fetch_raw("https://example.test/", mode="get")
    assert text == "caf� latte"


def test_get_mode_decoding_is_deterministic(monkeypatch):
    web = _FakeWeb(get_result=_FakeResponse(200, {}, b"\xff\xfe\x00abc"))
    install_fake_gl(monkeypatch, web)
    first = fetch_raw("https://example.test/", mode="get")
    for _ in range(10):
        assert fetch_raw("https://example.test/", mode="get") == first


def test_get_mode_accepts_a_str_body_too(monkeypatch):
    web = _FakeWeb(get_result=_FakeResponse(200, {}, "<p>already text</p>"))
    install_fake_gl(monkeypatch, web)
    _status, _headers, text = fetch_raw("https://example.test/", mode="get")
    assert text == "<p>already text</p>"


# ---------------------------------------------------------------------------
# Present (fake) SDK: render modes, and the blind spot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["html", "text"])
def test_render_mode_reports_a_synthetic_200_and_no_headers(monkeypatch, mode):
    """The documented limitation, pinned so it cannot be quietly 'fixed'.

    ``render`` returns a bare string.  Reporting anything other than
    ``(200, {})`` would be inventing information; reporting 200 is honest only
    because the limitation is documented and because body-based detection
    exists to cover it.
    """
    web = _FakeWeb(render_result="<html>rendered</html>")
    install_fake_gl(monkeypatch, web)
    status, headers, text = fetch_raw("https://example.test/", mode=mode)
    assert status == 200
    assert headers == {}
    assert text == "<html>rendered</html>"


def test_render_mode_passes_mode_and_wait_through(monkeypatch):
    web = _FakeWeb(render_result="x")
    install_fake_gl(monkeypatch, web)
    fetch_raw("https://example.test/", mode="text", wait_after_loaded="5s")
    assert web.calls == [
        (
            "render",
            "https://example.test/",
            {"mode": "text", "wait_after_loaded": "5s"},
        )
    ]


def test_a_rate_limit_page_in_render_mode_is_invisible_to_status_checking(
    monkeypatch,
):
    """Why body-based detection is not optional.

    The origin rate-limited us. ``render`` hands back the rate-limit page as
    an ordinary string, and the status WebAnchor can report is 200. Status
    checking is structurally blind here -- only ``detect`` can catch it.
    """
    from webanchor.detect import check_response
    from webanchor.errors import BotWallDetected
    from webanchor import Policy

    body = "<html><body><h1>Access denied</h1><p>Ray ID: abc</p></body></html>"
    web = _FakeWeb(render_result=body)
    install_fake_gl(monkeypatch, web)

    status, headers, text = fetch_raw("https://example.test/", mode="html")
    assert status == 200, "the transport cannot tell us otherwise"

    with pytest.raises(BotWallDetected):
        check_response(status, headers, text, Policy.default())


def test_render_mode_coerces_a_non_str_return(monkeypatch):
    web = _FakeWeb(render_result=12345)
    install_fake_gl(monkeypatch, web)
    _status, _headers, text = fetch_raw("https://example.test/", mode="html")
    assert text == "12345"


# ---------------------------------------------------------------------------
# Error wrapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["get", "html", "text"])
def test_any_sdk_exception_becomes_a_networkerror(monkeypatch, mode):
    web = _FakeWeb(boom=RuntimeError("connection reset by peer"))
    install_fake_gl(monkeypatch, web)
    with pytest.raises(NetworkError) as info:
        fetch_raw("https://example.test/", mode=mode)
    assert "connection reset by peer" in info.value.detail
    assert "RuntimeError" in info.value.detail
    assert info.value.url == "https://example.test/"


def test_the_original_exception_is_preserved_as_the_cause(monkeypatch):
    boom = ValueError("bad url")
    web = _FakeWeb(boom=boom)
    install_fake_gl(monkeypatch, web)
    with pytest.raises(NetworkError) as info:
        fetch_raw("https://example.test/", mode="get")
    assert info.value.__cause__ is boom


def test_the_wrapped_error_names_which_sdk_call_failed(monkeypatch):
    web = _FakeWeb(boom=RuntimeError("nope"))
    install_fake_gl(monkeypatch, web)
    with pytest.raises(NetworkError) as info:
        fetch_raw("https://example.test/", mode="get")
    assert "gl.nondet.web.get" in info.value.detail
    with pytest.raises(NetworkError) as info:
        fetch_raw("https://example.test/", mode="html")
    assert "gl.nondet.web.render" in info.value.detail


# ---------------------------------------------------------------------------
# The get_webpage compat shim
# ---------------------------------------------------------------------------


def test_get_webpage_delegates_to_render_and_returns_the_string(monkeypatch):
    web = _FakeWeb(render_result="<p>page</p>")
    install_fake_gl(monkeypatch, web)
    assert get_webpage("https://example.test/") == "<p>page</p>"
    assert web.calls[0][0] == "render"
    assert web.calls[0][2]["mode"] == "text"


def test_get_webpage_defaults_to_text_mode_like_the_old_sdk(monkeypatch):
    web = _FakeWeb(render_result="x")
    install_fake_gl(monkeypatch, web)
    get_webpage("https://example.test/")
    assert web.calls[0][2]["mode"] == "text"


def test_get_webpage_accepts_html_mode(monkeypatch):
    web = _FakeWeb(render_result="x")
    install_fake_gl(monkeypatch, web)
    get_webpage("https://example.test/", "html")
    assert web.calls[0][2]["mode"] == "html"


@pytest.mark.parametrize("mode", ["get", "json", ""])
def test_get_webpage_rejects_non_render_modes(mode):
    with pytest.raises(WebAnchorError) as info:
        get_webpage("https://example.test/", mode)
    assert "render" in info.value.detail


def test_the_shim_docstring_states_it_is_a_rename_not_a_reexport():
    doc = get_webpage.__doc__
    assert "v0.1.3" in doc
    assert "gl.nondet.web.render" in doc
    assert "no longer exists" in doc


def test_the_module_documents_the_render_blind_spot():
    doc = fetch_module.__doc__
    assert "429" in doc
    assert "no status" in doc.lower() or "there is no status" in doc.lower()
