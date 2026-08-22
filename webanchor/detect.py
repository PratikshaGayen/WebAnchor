"""Responses that must never reach the LLM.

This module is the "fails loudly instead of producing a verdict from a 429
page" half of the WebAnchor thesis.  Everything else in the library makes a
*good* response comparable; this makes a *bad* response impossible to mistake
for a good one.

Why status checking alone is not enough
---------------------------------------
The obvious defense is to look at the HTTP status and stop on 4xx/5xx.  It is
necessary and it is nowhere near sufficient, because the two most common ways
a web read goes wrong in practice both arrive as **HTTP 200**:

* A **bot wall / challenge**.  Cloudflare's interstitial, Incapsula's block
  page, an hCaptcha gate: these are ``200 OK`` with a body that says "Just a
  moment..." and a spinner.  A contract that hands that to an LLM and asks
  "is the order shipped?" gets an answer.  The answer is derived from a
  challenge page.  Nothing in the pipeline downstream of here can tell.
* A **soft error page**.  A CMS that renders its 404 template with a ``200``
  status, a maintenance page, a "service temporarily unavailable" splash.
  Same failure, different wrapper.

Both are *worse* than a hard error, because a hard error at least raises
somewhere.  These produce a plausible-looking verdict from content that has
nothing to do with the question, and they do it identically on every
validator -- so consensus **succeeds**, and the contract commits a wrong fact
with full agreement.  That is the single most expensive failure this library
can prevent, which is why detection lives in the default path rather than
behind a flag.

The false-positive budget is not zero
-------------------------------------
A library that refuses valid pages is unusable, and "refuses valid pages" is
not a smaller bug than "accepts challenge pages" -- it is the same bug facing
the other way.  A news article *about* Cloudflare outages legitimately
contains the string "checking your browser".  A blog post about HTTP status
codes legitimately contains "404" and "page not found".  Neither may raise.

Markers are therefore split into two tiers, and the tiering is the whole
design:

* :data:`BOT_WALL_STRONG_MARKERS` -- strings that are *machinery*, not prose:
  a Cloudflare challenge script path, an Incapsula resource name, a widget
  container id.  Their presence is dispositive at any body length, because no
  article writes ``cf-browser-verification`` in a sentence.
* :data:`BOT_WALL_WEAK_MARKERS` -- English phrases a challenge page shows a
  human.  Any of them can appear in real prose, so they fire **only** on a
  body whose *visible text* is short enough to be a challenge page and not an
  article.  See :data:`BOT_WALL_MAX_VISIBLE_CHARS`.

Soft-error detection is weak-tier only, and requires **two** signals: a short
visible body *and* an error phrase in the head region (the ``<title>`` or the
first :data:`SOFT_ERROR_HEAD_CHARS` characters of visible text).  A long
article that mentions 404 fails both conditions.

The ``Server:``+shape branch is opt-in, and here is the data that made it so
--------------------------------------------------------------------------
There is a third, narrower tier: when ``policy.detect_bot_wall_server_hint``
is ``True``, a short visible body on a ``Server:`` header naming a
challenge-capable edge (:data:`CHALLENGE_SERVER_TOKENS`) is checked against
:data:`CHALLENGE_SHAPE_MARKERS` -- phrases like ``"please wait"`` or
``"redirecting"`` that are challenge-shaped on their own but common in
ordinary small pages.

This tier defaults to **off** (``Policy.default().detect_bot_wall_server_hint
is False``; only :meth:`webanchor.Policy.strict` turns it on), and the reason
is measured, not argued: the M5 benchmark (``BENCHMARK.md``,
"Cloudflare-branch finding") ran this branch against
``tests/fixtures/corpus/small_legit/`` -- five tiny, realistic non-challenge
pages (a landing stub, a redirect stub, a status endpoint, a "coming soon"
page, a minimal FAQ) -- fronted by a synthetic ``Server: cloudflare`` header.
**2 of 5** raised :class:`~webanchor.errors.BotWallDetected`:
``coming_soon.html`` on ``"please wait"``, ``redirect_stub.html`` on
``"redirecting"``. Against the same benchmark's one real bot-wall fixture,
``tests/fixtures/cloudflare_challenge.html``, the strong-marker tier alone
already fires (``cf-browser-verification``) -- this branch was never the
only signal that caught it. Measured: a 40% false-positive rate on that
corpus for a 0% measured marginal benefit. That is the textbook case for
opt-in rather than default-on, and :meth:`webanchor.Policy.strict` -- the
policy for callers who know their page and accept aggressive-heuristic
trade-offs -- is where it lives.

Every raise names the marker that fired
---------------------------------------
:class:`~webanchor.errors.BotWallDetected` and
:class:`~webanchor.errors.SoftErrorPage` always carry the specific marker in
``detail``.  A typed error that does not say *why* it fired is only half
useful: the contract author cannot tell a genuine bot wall from a
false positive, cannot tune, and cannot report the bug.  Marker ordering
inside each tuple is therefore load-bearing -- first match wins, so the same
page always names the same marker on every validator.

R3 and R6
---------
Every marker list is a **tuple**, never a set: set iteration order is not a
stable contract, and a detail string that names a different marker on a
different build is a divergence in the error path.

Every marker list and every threshold here is output-affecting in the harshest
sense -- adding one marker flips some page from "returns Evidence" to
"raises".  They are covered by :data:`webanchor.behavior.BEHAVIOR_VERSION`,
which is folded into ``policy_id`` and therefore into every fingerprint.
**Adding, removing or reordering a marker requires a BEHAVIOR_VERSION bump.**
See the bump checklist in :mod:`webanchor.behavior`, item 11.
"""

import re
from typing import Mapping, Optional

from .errors import (
    BotWallDetected,
    ContentTooLarge,
    EmptyContent,
    SoftErrorPage,
    from_status,
)
from .html_strip import reject_non_textual
from .policy import Policy

__all__ = [
    "check_response",
    "visible_text",
    "BOT_WALL_STRONG_MARKERS",
    "BOT_WALL_WEAK_MARKERS",
    "CHALLENGE_SERVER_TOKENS",
    "CHALLENGE_SHAPE_MARKERS",
    "SOFT_ERROR_PHRASES",
    "BOT_WALL_MAX_VISIBLE_CHARS",
    "SOFT_ERROR_MAX_VISIBLE_CHARS",
    "SOFT_ERROR_HEAD_CHARS",
]


# ---------------------------------------------------------------------------
# Marker tables.  Tuples, not sets (R3).  Covered by BEHAVIOR_VERSION (R6).
# ---------------------------------------------------------------------------

#: Machinery markers.  Dispositive at any body length.
#:
#: Every entry is a literal that a challenge platform emits into the DOM and
#: that English prose does not contain: a script path, a resource name, a
#: cookie or query-parameter name, a widget container id, a vendor page title.
#: Lowercase; matched against a lowercased body.  Order is match priority.
BOT_WALL_STRONG_MARKERS: tuple[str, ...] = (
    "attention required! | cloudflare",
    "cf-browser-verification",
    "/cdn-cgi/challenge-platform",
    "__cf_chl",
    "cf_chl_opt",
    "cf-challenge-running",
    "request unsuccessful. incapsula incident id",
    "_incapsula_resource",
    "g-recaptcha",
    "h-captcha",
    "hcaptcha.com/captcha",
    "www.google.com/recaptcha/api.js",
    "px-captcha",
    "distil_r_captcha",
    "perimeterx",
    "datadome",
)

#: Human-facing challenge phrases.  Fire ONLY on a short visible body.
#:
#: Each of these can legitimately appear in an article, which is why length
#: gating is mandatory rather than a refinement.  Lowercase; matched against
#: lowercased *visible text*, not raw markup -- a phrase buried in a script
#: string on an otherwise normal page is not a challenge.
BOT_WALL_WEAK_MARKERS: tuple[str, ...] = (
    "just a moment...",
    "checking your browser",
    "checking if the site connection is secure",
    "please enable javascript and cookies",
    "enable javascript and cookies to continue",
    "verify you are human",
    "verifying you are human",
    "please verify you are a human",
    "complete the security check",
    "ddos protection by",
    "access denied",
    "you have been blocked",
    "unusual traffic from your computer network",
    "recaptcha",
    "hcaptcha",
    "captcha",
)

#: ``Server:`` header values that identify a challenge-capable edge.
#:
#: A match is NOT on its own evidence of anything -- a large share of the
#: legitimate web is behind Cloudflare.  It only lowers the bar for the weak
#: tier, and only in combination with a short, challenge-shaped body.
CHALLENGE_SERVER_TOKENS: tuple[str, ...] = (
    "cloudflare",
    "incapsula",
    "akamaighost",
)

#: Body shapes that, on a challenge-capable edge with a short body, indicate a
#: challenge rather than a small real page.
CHALLENGE_SHAPE_MARKERS: tuple[str, ...] = (
    "enable javascript",
    "enable cookies",
    "ray id",
    "security of your connection",
    "review the security of your connection",
    "needs to review the security",
    "please wait",
    "one more step",
    "redirecting",
)

#: Error phrasing that, in the head region of a SHORT page, means the 200 is
#: a lie.  Ordered most-specific first so the detail names the useful marker.
SOFT_ERROR_PHRASES: tuple[str, ...] = (
    "page not found",
    "404 not found",
    "error 404",
    "404 error",
    "not found",
    "no longer exists",
    "this page doesn't exist",
    "this page does not exist",
    "we'll be back",
    "we will be back",
    "temporarily unavailable",
    "service unavailable",
    "under maintenance",
    "site maintenance",
    "something went wrong",
    "an error occurred",
    "404",
    "error",
)

#: Above this many characters of *visible text*, a weak bot-wall marker is
#: treated as prose.  A Cloudflare interstitial renders a few hundred
#: characters; an article that mentions one runs to thousands.
BOT_WALL_MAX_VISIBLE_CHARS = 1200

#: Above this many characters of visible text, error phrasing is treated as
#: an article *about* errors.  Tighter than the bot-wall budget because the
#: phrases are far more common in ordinary prose.
SOFT_ERROR_MAX_VISIBLE_CHARS = 600

#: How much of the visible text counts as "the head".  A real error page leads
#: with the error; an article that mentions one buries it in the body.
SOFT_ERROR_HEAD_CHARS = 200


_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)


def visible_text(body: str) -> str:
    """A cheap, total approximation of what a reader would see.

    Deliberately *not* :func:`webanchor.html_strip.strip_html`.  The stripper
    raises on empty, oversized and non-textual input, and detection has to run
    **before** those checks -- a challenge page that also happens to be empty
    must be reported as a challenge, not as an empty page, because the two
    call for different actions from the contract author.  This function
    therefore never raises and never rejects: it drops script/style/noscript
    bodies and comments, deletes remaining tags, and collapses whitespace.

    It is used only for *length and phrase* judgements, never for anything
    that reaches a fingerprint, so its approximations cost nothing downstream.
    """
    text = _COMMENT_RE.sub(" ", body)
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _title_text(body: str) -> str:
    match = _TITLE_RE.search(body)
    if match is None:
        return ""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", match.group(1))).strip()


def _server_token(headers: Mapping[str, str]) -> Optional[str]:
    """The challenge-capable edge named by ``Server:``, if any.

    Header names are case-insensitive per RFC 9110, and the mapping handed to
    us may come from anywhere, so the lookup is done by lowercasing keys
    rather than by assuming a canonical spelling.  Keys are visited in the
    mapping's own order and the first ``server`` wins; a well-formed response
    has exactly one.
    """
    for key, value in headers.items():
        if key.lower() == "server":
            lowered = str(value).lower()
            for token in CHALLENGE_SERVER_TOKENS:
                if token in lowered:
                    return token
            return None
    return None


def _check_bot_wall(
    headers: Mapping[str, str],
    lowered_body: str,
    lowered_visible: str,
    url: Optional[str],
    policy: Policy,
) -> None:
    """Raise :class:`BotWallDetected` naming the first marker that fires."""
    for marker in BOT_WALL_STRONG_MARKERS:
        if marker in lowered_body:
            raise BotWallDetected(
                "challenge marker {0!r} found in the response body; this is a "
                "bot wall served as HTTP 200, not the page".format(marker),
                url=url,
            )

    visible_length = len(lowered_visible)
    if visible_length > BOT_WALL_MAX_VISIBLE_CHARS:
        # Long enough to be an article. Weak markers are prose here, and this
        # early return is what keeps a piece *about* bot walls readable.
        return

    for marker in BOT_WALL_WEAK_MARKERS:
        if marker in lowered_visible:
            raise BotWallDetected(
                "challenge phrase {0!r} found in a {1}-character body; a page "
                "this short saying this is a bot wall, not content".format(
                    marker, visible_length
                ),
                url=url,
            )

    if not policy.detect_bot_wall_server_hint:
        # Opt-in only (Policy.strict() turns it on). Measured on the M5
        # benchmark corpus (BENCHMARK.md, "Cloudflare-branch finding"): 2/5
        # small legitimate pages fronted by a challenge-capable edge raised
        # BotWallDetected on ordinary phrases ("please wait", "redirecting"),
        # while the strong- and weak-marker tiers above already caught the
        # one real bot-wall fixture in this repository without it -- a
        # measured 40% false-positive rate for 0% measured marginal benefit.
        return

    server = _server_token(headers)
    if server is None:
        return
    for marker in CHALLENGE_SHAPE_MARKERS:
        if marker in lowered_visible:
            raise BotWallDetected(
                "Server: {0} served a {1}-character body containing {2!r}; "
                "this is challenge-shaped, not content".format(
                    server, visible_length, marker
                ),
                url=url,
            )


def _check_soft_error(
    body_text: str, visible: str, url: Optional[str]
) -> None:
    """Raise :class:`SoftErrorPage` only when TWO independent signals agree.

    Signal one: the visible text is shorter than
    :data:`SOFT_ERROR_MAX_VISIBLE_CHARS`.  Signal two: an error phrase appears
    in the ``<title>`` or in the first :data:`SOFT_ERROR_HEAD_CHARS`
    characters of visible text.

    Requiring both is the entire defense against eating a legitimate article
    that discusses HTTP errors: such an article fails the length test, and
    even a short one usually buries the phrase past the head.  Conservatism is
    the correct bias here -- a wrongly-refused page is a visible, debuggable
    failure, but only if the library is still trusted enough to be used.
    """
    if len(visible) > SOFT_ERROR_MAX_VISIBLE_CHARS:
        return

    title = _title_text(body_text).lower()
    head = visible[:SOFT_ERROR_HEAD_CHARS].lower()

    for phrase in SOFT_ERROR_PHRASES:
        where = None
        if phrase in title:
            where = "the page title"
        elif phrase in head:
            where = "the first {0} characters".format(SOFT_ERROR_HEAD_CHARS)
        if where is None:
            continue
        raise SoftErrorPage(
            "error phrase {0!r} in {1} of a {2}-character page; this is an "
            "error page served as HTTP 200, not content".format(
                phrase, where, len(visible)
            ),
            url=url,
        )


def check_response(
    status: int,
    headers: Mapping[str, str],
    body_text: str,
    policy: Policy,
    *,
    url: Optional[str] = None,
) -> None:
    """Raise if this response must not reach the LLM; return ``None`` if usable.

    Returning ``None`` is the success signal.  There is no boolean and no
    result object on purpose: a caller cannot forget to check an exception
    the way it can forget to check a flag, and blueprint rule R4 wants the
    failure path loud.

    Checks run in this fixed order, and the order is the contract -- the
    *first* thing wrong with a response is what gets reported, so two
    validators looking at the same bytes raise the same error with the same
    detail:

    1. **Status.** Delegated to :func:`webanchor.errors.from_status`, which
       owns the status taxonomy; duplicating the mapping here would let the
       two drift.
    2. **Bot wall / challenge**, on an otherwise-fine 2xx.  Before the soft
       error check, because a challenge page often says "Access denied" and
       "challenge" is the more actionable diagnosis than "error page".
    3. **Soft error page**, on an otherwise-fine 2xx.
    4. **Textuality** -- NUL bytes or too many C0 controls, per
       ``policy.max_control_char_ratio``.  After the page-kind checks because
       "this is a bot wall" is more useful than "this is not text", and a
       mis-decoded challenge page can be both.
    5. **Size** -- over ``policy.max_content_bytes``.
    6. **Emptiness** -- empty or whitespace-only.  Last: an empty body that is
       *also* a challenge should be reported as the challenge.

    Args:
        status: HTTP status. In ``render`` fetch mode the SDK does not expose
            one and the caller passes ``200``; see :mod:`webanchor.fetch`.
            That is precisely why checks 2 and 3 exist.
        headers: Response headers, matched case-insensitively. May be empty --
            it always is in render mode, which weakens only the
            ``Server:``-assisted branch, not the body-based ones.
        body_text: The response body as text, **before** normalization.
            Markers live in markup (``cf-browser-verification`` is a class
            name), so stripping first would destroy the evidence.
        policy: Supplies the textuality and size limits, and reaches
            ``policy_id`` so two validators cannot silently apply different
            ones. Also gates the ``Server:``+shape-marker branch via
            ``policy.detect_bot_wall_server_hint`` -- off by
            :meth:`~webanchor.Policy.default`, on by
            :meth:`~webanchor.Policy.strict`; see the module docstring.
        url: Attached to every raised error for diagnosis.

    Raises:
        RateLimited, NotFound, Forbidden, UpstreamUnavailable,
        UnexpectedStatus: from the status mapping.
        BotWallDetected: a challenge or interstitial, naming the marker.
        SoftErrorPage: an error page served as 200, naming the phrase.
        NotTextual: NUL bytes, or C0 controls over the policy ratio.
        ContentTooLarge: body over ``policy.max_content_bytes``.
        EmptyContent: body empty or whitespace-only.
    """
    status_error = from_status(status, url=url)
    if status_error is not None:
        raise status_error

    visible = visible_text(body_text)
    lowered_body = body_text.lower()
    lowered_visible = visible.lower()

    _check_bot_wall(headers, lowered_body, lowered_visible, url, policy)
    _check_soft_error(body_text, visible, url)

    reject_non_textual(body_text, policy, url=url)

    size = len(body_text.encode("utf-8"))
    if size > policy.max_content_bytes:
        raise ContentTooLarge(
            "response is {0} bytes, over the {1}-byte policy limit".format(
                size, policy.max_content_bytes
            ),
            url=url,
        )

    if not body_text.strip():
        raise EmptyContent(
            "response body is empty or whitespace-only; there is no evidence "
            "in it to anchor",
            url=url,
        )
