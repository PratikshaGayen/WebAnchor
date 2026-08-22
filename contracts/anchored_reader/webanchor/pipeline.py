"""The normalization path, and the two public entry points built on it.

:func:`normalize` is the pure M1-M3 core: raw HTML in, anchorable text out.
:func:`anchor_html` wraps it with M4 detection, fingerprinting and an
:class:`~webanchor.Evidence`; :func:`anchor` is :func:`anchor_html` with a
GenLayer fetch in front of it.  There is one pipeline implementation, not two
-- see :func:`anchor_html` for why that is a requirement and not a tidiness
preference.

:func:`normalize` is the whole M1-M3 library composed in one place.  It touches
no network, no clock and no GenLayer runtime -- it is a pure function of
``(html, policy)``, which is what makes the entire divergence story testable
without a validator set.

Stage order, and why it is not arbitrary
----------------------------------------
``strip_html`` -> ``canonicalize_text`` -> ``quantize_timestamps`` ->
``band_numbers``

1. **strip_html first.** Everything downstream operates on prose.  Running a
   number matcher over raw markup would band the digits inside
   ``<h1 class="col-6">`` and inside every URL and hex color in an attribute.
2. **canonicalize_text second.** The timestamp and number matchers are written
   against ASCII separators and ASCII digits-with-ASCII-punctuation.  A page
   that emits a narrow no-break space inside ``1 234,56`` or an en dash inside
   a date range must be folded to ASCII *before* those matchers run, or the
   matchers see two different strings for one rendered value -- which is the
   exact divergence they exist to remove.
3. **quantize_timestamps third, and this is the load-bearing ordering
   decision.** ``2024-04-12`` is three numbers to the number matcher.  If
   banding ran first it would rewrite the date's digits (rounding ``2024`` to
   ``2020`` under three significant digits) and the timestamp matcher would
   then find nothing to redact -- a mangled date *and* a leaked timestamp,
   from one wrong ordering.  Timestamps are consumed first, replaced by
   digit-free tokens (``[timestamp]``, ``[relative-time]``), and only then is
   the remaining text handed to the number matcher.
   ``test_timestamps_are_consumed_before_numbers`` pins this.
4. **band_numbers last**, over text that no longer contains dates.

Idempotency
-----------
:func:`normalize` output is idempotent under ``number_band_mode`` ``none``
(the default policy) and ``significant`` (the strict policy), in the sense
that canonicalizing the returned text again is a no-op.  It is **not**
idempotent under ``grid``, because a band token ``[1200~1300]`` contains
two fresh numbers that a second pass would band again.  That boundary is
tested in ``tests/test_pipeline.py`` rather than asserted away.
"""

from typing import Mapping, Optional

from .evidence import Evidence
from .fingerprint import fingerprint
from .html_strip import strip_html
from .numbers import band_numbers
from .policy import Policy
from .text import canonicalize_text
from .timestamps import quantize_timestamps

__all__ = ["normalize", "anchor", "anchor_html"]


def normalize(html: str, policy: Policy) -> tuple[str, dict[str, str]]:
    """Run the full pure normalization path over ``html``.

    Returns the normalized text and the ``bands`` mapping from
    :func:`webanchor.numbers.band_numbers` -- original matched substring to
    replacement -- which is what an :class:`~webanchor.Evidence` carries as
    its audit trail.  With ``number_band_mode="none"`` the mapping is empty.

    Raises:
        ContentTooLarge, NotTextual, EmptyContent: propagated from
            :func:`~webanchor.html_strip.strip_html`.  They are not caught
            here: a page that cannot be stripped has no evidence in it, and
            blueprint rule R4 forbids returning a partial result in that case.
    """
    text = strip_html(html, policy)
    text = canonicalize_text(text, policy)
    text = quantize_timestamps(text, policy)
    return band_numbers(text, policy)


def anchor_html(
    html: str,
    url: str,
    policy: Optional[Policy] = None,
    *,
    status: int = 200,
    headers: Optional[Mapping[str, str]] = None,
    fetched_bucket: int = 0,
) -> Evidence:
    """Anchor HTML you already have: detection, normalization, fingerprint.

    This is :func:`anchor` with the network removed and **nothing else
    changed**.  It is not a test helper that approximates the real path -- it
    *is* the real path; :func:`anchor` is a thin fetch in front of a call to
    this function.  Keeping it that way is deliberate: a demonstration path
    that merely resembled production would prove nothing about production, and
    the entire M5 divergence corpus, every end-to-end test, and every
    off-chain demo of this library run through here.  If the two ever diverge,
    the tests stop testing the shipped code.

    Args:
        html: The response body as text.  Passed to
            :func:`webanchor.detect.check_response` **before** stripping,
            because challenge markers live in markup that stripping deletes.
        url: Recorded on the :class:`~webanchor.Evidence` and attached to any
            error raised, for diagnosis.  Not fetched.
        policy: Defaults to :meth:`webanchor.Policy.default`.  The default is
            constructed here rather than as a parameter default so that a
            future change to ``Policy.default()`` cannot be frozen into this
            function's signature at import time.
        status: The status that accompanied ``html``, if you have one.  The
            ``200`` default is the honest value for content that arrived
            through a renderer, which does not expose a status at all -- see
            :mod:`webanchor.fetch`.
        headers: Response headers, if you have them.  Optional because render
            mode never does; their absence only weakens the
            ``Server:``-assisted branch of bot-wall detection.
        fetched_bucket: A caller-supplied quantized fetch time, defaulting to
            ``0``.  **This library never reads a clock**, and that is a design
            requirement rather than an omission: a value derived from
            ``time.time()`` inside the library would differ on every validator
            and would be folded straight into the consensus artifact,
            manufacturing the exact divergence WebAnchor exists to remove.  A
            library that reads wall-clock time to build a consensus artifact
            is self-defeating.  If you want a fetch bucket in the evidence,
            quantize a clock reading *outside* the library -- ideally one the
            validators already agree on, such as a block timestamp -- and pass
            it in.  Then, and only then, is it a shared fact rather than a
            per-node reading.  Note that a bucket still straddles: see
            :mod:`webanchor.behavior`.

    Returns:
        An :class:`~webanchor.Evidence` whose ``fingerprint`` is what
        ``gl.eq_principle.strict_eq`` compares.

    Raises:
        WebAnchorError: any subclass, from detection
            (:func:`~webanchor.detect.check_response`) or from normalization
            (:func:`normalize`).  Nothing is caught and nothing is softened --
            R4 forbids returning a partial Evidence, because a partial
            Evidence is indistinguishable from a real one once it is in
            calldata.
    """
    from .detect import check_response

    if policy is None:
        policy = Policy.default()

    check_response(status, headers or {}, html, policy, url=url)

    text, bands = normalize(html, policy)
    policy_id = policy.policy_id
    return Evidence(
        url=url,
        status=status,
        fingerprint=fingerprint(text, policy_id),
        policy_id=policy_id,
        text=text,
        bands=bands,
        fetched_bucket=fetched_bucket,
    )


def anchor(
    url: str,
    policy: Optional[Policy] = None,
    *,
    mode: str = "html",
    wait_after_loaded: Optional[str] = None,
    fetched_bucket: int = 0,
) -> Evidence:
    """Fetch ``url`` and anchor it: the full path, network included.

    ``fetch_raw`` -> ``check_response`` -> ``normalize`` -> ``fingerprint`` ->
    :class:`~webanchor.Evidence`.  Every step after the fetch is delegated to
    :func:`anchor_html`, which is the same function the offline tests and the
    M5 corpus exercise, so there is exactly one implementation of the pipeline.

    ``webanchor.fetch`` is imported **inside this function body**, never at
    module scope.  ``pipeline`` is imported by ``webanchor/__init__.py``, so a
    module-scope import here would drag the GenLayer dependency into
    ``import webanchor`` and break the library on every machine without a
    GenVM -- blueprint rule R2.  The cost is deferred failure; the benefit is
    that ``import webanchor`` and ``anchor_html`` work everywhere, and only
    ``anchor`` itself requires the runtime.

    Args:
        url: The absolute URL to read.
        policy: Defaults to :meth:`webanchor.Policy.default`.
        mode: ``"html"``/``"text"`` (renderer) or ``"get"`` (raw HTTP).  Read
            :mod:`webanchor.fetch` on the render-mode blind spot: render mode
            reports no status, so a 429 is detectable only from the body.
        wait_after_loaded: Renderer settle time, e.g. ``"5s"``.
        fetched_bucket: See :func:`anchor_html`.  Still not a clock read.

    Returns:
        An :class:`~webanchor.Evidence` ready for
        ``gl.eq_principle.strict_eq``.

    Raises:
        WebAnchorError: if the GenLayer SDK is absent -- this function, alone
            in the library, requires a GenVM contract execution.  Use
            :func:`anchor_html` off-chain.
        NetworkError: the SDK fetch failed.
        Everything :func:`anchor_html` raises.
    """
    from .fetch import fetch_raw

    status, headers, body = fetch_raw(
        url, mode=mode, wait_after_loaded=wait_after_loaded
    )
    return anchor_html(
        body,
        url,
        policy,
        status=status,
        headers=headers,
        fetched_bucket=fetched_bucket,
    )
