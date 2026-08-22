"""The only module in WebAnchor permitted to touch GenLayer.

Blueprint rule R2 confines every ``genlayer`` / ``gl`` reference to this file,
and confines it further to the *inside of function bodies*.  Both halves
matter:

* **Only this file**, so the rest of the library is a pure function of
  strings and is testable with plain ``pytest`` on any machine -- no GenVM,
  no node, no network.  ``tests/test_constraints.py`` parses the package with
  ``ast`` and enforces this rather than trusting review.
* **Only inside function bodies**, so that ``import webanchor`` succeeds on a
  developer laptop with no SDK installed.  A module-scope ``import genlayer``
  here would propagate an ``ImportError`` through
  ``webanchor.pipeline.anchor`` to ``webanchor/__init__.py`` and make the
  whole library unimportable off-chain, which would take the entire test
  suite with it.  The failure is deferred to the moment somebody actually
  *calls* :func:`fetch_raw`, where it is both unavoidable and actionable.

The SDK surface this targets
----------------------------
GenLayer SDK v0.1.3+ / executors v0.3:

* ``gl.nondet.web.get(url)`` -> an object with ``.status``, ``.headers`` and
  ``.body`` (bytes).
* ``gl.nondet.web.render(url, mode="text"|"html", wait_after_loaded="5s")``
  -> ``str``.

``gl.get_webpage`` is the **pre-v0.1.3 name** and no longer exists.  See
:func:`get_webpage` below for the compatibility shim and why it is a shim
rather than a re-export.

The render-mode blind spot, stated plainly
------------------------------------------
``render`` returns a **string and nothing else**.  There is no status code and
there are no headers, because the headless browser has already followed
redirects, executed scripts and settled on a DOM.  WebAnchor therefore reports
``status=200`` and ``headers={}`` for every render-mode fetch.

That is not a placeholder to be fixed later; it is a limitation of the
transport, and it changes the threat model:

* A 429 arriving in render mode is **invisible to status checking**.  The
  rate-limit page renders, comes back as a perfectly ordinary string, and
  ``from_status(200)`` says everything is fine.
* Likewise a 404 that the origin renders as a styled error page, and a 403
  block page.

This is exactly why :mod:`webanchor.detect` does body-based bot-wall and
soft-error detection instead of stopping at the status line.  In ``get`` mode
those body checks are a second line of defense; in ``render`` mode they are
the *only* line of defense, and the ``Server:``-header branch of bot-wall
detection is unavailable too, because there are no headers.

Practical consequence: prefer ``mode="get"`` when the page does not need
JavaScript, because it gives detection strictly more to work with.  Use
``render`` when the content genuinely requires a browser, and understand that
you are trading status visibility for it.
"""

from typing import Optional

from .errors import NetworkError, WebAnchorError

__all__ = ["fetch_raw", "get_webpage", "FETCH_MODES", "RENDER_MODES"]

#: Fetch modes accepted by :func:`fetch_raw`, in a fixed tuple (R3).
FETCH_MODES: tuple[str, ...] = ("html", "text", "get")

#: The subset of :data:`FETCH_MODES` that routes through ``web.render`` and
#: therefore carries the no-status/no-headers limitation documented above.
RENDER_MODES: tuple[str, ...] = ("html", "text")

_IMPORT_HELP = (
    "webanchor.fetch requires the GenLayer SDK, which is only present inside "
    "a GenVM contract execution. This function cannot work on a developer "
    "machine or in CI. Everything else in WebAnchor is pure and runs without "
    "it: to exercise the full pipeline off-chain, pass HTML directly to "
    "webanchor.anchor_html(html, url) instead of calling webanchor.anchor(url)."
)


def _import_gl():
    """Import the ``gl`` binding lazily, or raise a WebAnchorError that helps.

    A bare ``ImportError`` reaching a contract author says "No module named
    'genlayer'", which is true and useless.  It does not say that the rest of
    the library works fine without it, and it does not point at
    :func:`webanchor.anchor_html`, which is what the caller almost always
    wants when they hit this off-chain.  Rule R4 asks for loud failures;
    a loud failure that also says what to do instead is strictly better.
    """
    try:
        from genlayer import gl  # noqa: F401  (imported for the caller)
    except ImportError as exc:
        raise WebAnchorError("{0} (import failed: {1})".format(_IMPORT_HELP, exc))
    return gl


def fetch_raw(
    url: str,
    *,
    mode: str = "html",
    wait_after_loaded: Optional[str] = None,
) -> tuple[int, dict[str, str], str]:
    """Fetch ``url`` through the GenLayer non-deterministic web block.

    Args:
        url: The absolute URL to read.
        mode: ``"html"`` or ``"text"`` route through ``gl.nondet.web.render``
            and return rendered DOM text; ``"get"`` routes through
            ``gl.nondet.web.get`` and returns the raw body decoded as UTF-8
            with ``errors="replace"``.  Replacement rather than strict
            decoding is deliberate: a mis-encoded byte must not raise here and
            deprive :mod:`webanchor.detect` of the chance to say *why* the
            response is unusable, and the resulting U+FFFD characters are
            deterministic, so no divergence is introduced.
        wait_after_loaded: Passed straight to ``render`` (e.g. ``"5s"``).
            Ignored in ``get`` mode, which has no page lifecycle to wait on.
            Note that a longer wait widens the wall-clock spread between
            validators, which increases residual divergence for any quantized
            value on the page -- see :mod:`webanchor.behavior`.

    Returns:
        ``(status, headers, body_text)``.  In the two render modes the SDK
        exposes neither a status nor headers, so this is **always**
        ``(200, {}, text)``.  Read the module docstring before relying on that
        200: it means "render mode", not "the origin said OK".

    Raises:
        WebAnchorError: ``genlayer`` cannot be imported -- you are not inside
            a GenVM contract.  The message names
            :func:`webanchor.anchor_html` as the off-chain alternative.
        WebAnchorError: ``mode`` is not one of :data:`FETCH_MODES`.  Raised
            *before* the SDK import so the message is about the typo rather
            than about a missing dependency.
        NetworkError: the SDK call itself failed, for any reason.  The
            original exception's type and message are preserved in ``detail``
            rather than discarded -- a typed error that erases the cause
            replaces one debugging problem with another.
    """
    if mode not in FETCH_MODES:
        raise WebAnchorError(
            "unknown fetch mode {0!r}; expected one of {1}".format(
                mode, list(FETCH_MODES)
            ),
            url=url,
        )

    gl = _import_gl()

    if mode == "get":
        try:
            response = gl.nondet.web.get(url)
        except Exception as exc:
            raise NetworkError(
                "gl.nondet.web.get failed: {0}: {1}".format(
                    type(exc).__name__, exc
                ),
                url=url,
            ) from exc
        status = int(response.status)
        headers = {str(k): str(v) for k, v in dict(response.headers).items()}
        body = response.body
        if isinstance(body, (bytes, bytearray)):
            text = bytes(body).decode("utf-8", errors="replace")
        else:
            text = str(body)
        return status, headers, text

    try:
        rendered = gl.nondet.web.render(
            url, mode=mode, wait_after_loaded=wait_after_loaded
        )
    except Exception as exc:
        raise NetworkError(
            "gl.nondet.web.render failed: {0}: {1}".format(
                type(exc).__name__, exc
            ),
            url=url,
        ) from exc
    # No status, no headers -- see "The render-mode blind spot" above.
    return 200, {}, str(rendered)


def get_webpage(url: str, mode: str = "text") -> str:
    """Compatibility shim for the removed ``gl.get_webpage``.

    ``gl.get_webpage(url, mode)`` was the GenLayer SDK's page-fetch entry point
    **before v0.1.3**.  It no longer exists: the current SDK spells it
    ``gl.nondet.web.render(url, mode=...)``.  Contracts and tutorials written
    against the old name are still in circulation, so this function exists so
    that the familiar call site keeps working -- it is a *rename adapter*, not
    a re-export, and there is no ``gl.get_webpage`` behind it to fall back to.

    It delegates to :func:`fetch_raw`, which delegates to
    ``gl.nondet.web.render``, and returns only the body string, discarding the
    synthetic ``200`` and the empty header dict that render mode produces.

    Prefer :func:`webanchor.anchor` over this. This returns a raw string with
    no detection and no normalization applied -- the two things WebAnchor is
    for. It is here for familiarity during a port, not as a recommended API.

    Raises:
        WebAnchorError: ``genlayer`` is not importable, or ``mode`` is not a
            render mode.
        NetworkError: the underlying SDK call failed.
    """
    if mode not in RENDER_MODES:
        raise WebAnchorError(
            "get_webpage mode must be one of {0}, got {1!r}; it maps onto "
            "gl.nondet.web.render, which has no other modes".format(
                list(RENDER_MODES), mode
            ),
            url=url,
        )
    _status, _headers, text = fetch_raw(url, mode=mode)
    return text
