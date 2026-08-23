"""The single integer that versions WebAnchor's *behavior*, not its API.

Blueprint rule R6: **every constant that can change output must be versioned
into ``policy_id``.**

Some normalization behavior cannot reasonably live in :class:`~webanchor.Policy`
as a tunable field -- the set of HTML tags whose subtree is dropped, the set of
tags that emit a line boundary, the exact whitespace-collapsing rules, the
canonical rendering of a quantized timestamp.  Those are implementation
constants, but they are *output-affecting* implementation constants, and that
makes them a consensus hazard: a leader on WebAnchor 0.1.0 and a validator on
0.2.0 would normalize the same bytes differently, produce different
fingerprints, and have no way to attribute the disagreement to a version skew
rather than to the page changing underneath them.  Worse, in the raising cases
(control-character ratio, size caps) one node returns text while the other
raises -- silent, asymmetric divergence.

``BEHAVIOR_VERSION`` closes that hole.  It is folded into
:meth:`webanchor.Policy.canonical_json` and therefore into every ``policy_id``
and every fingerprint.  Two nodes running different behavior versions produce
*visibly* different policy ids, which is a diagnosable failure instead of a
silent one.

Bump the integer whenever a change alters the output of the normalization
pipeline for **some** input
=====================================================================

"Some input" is the bar, not "typical input".  If you cannot prove that every
possible input produces byte-identical output before and after your change,
bump it.  Concretely, bump when you change any of:

1. ``html_strip.DROP_SUBTREE_TAGS`` -- adding or removing a dropped subtree.
2. ``html_strip.BOUNDARY_TAGS`` -- adding or removing a newline-emitting tag.
3. ``html_strip._VOID_TAGS`` -- void-element handling shifts the tag stack.
4. Depth-cap *semantics* (the cap's value is ``Policy.max_tag_depth``, but
   what happens on overflow is behavior).
5. Control-character handling: which characters count as controls, whether
   NUL raises, how the ratio is compared against ``Policy.max_control_char_ratio``.
6. Whitespace rules: horizontal-whitespace collapsing, per-line stripping,
   blank-line squeezing, line-ending normalization.
7. The unicode fold table in ``text.py`` -- dashes, quotes, primes,
   zero-width characters, space variants.
8. Number detection or rendering in ``numbers.py``: the match pattern, the
   thousands-separator ambiguity rule, the rounding mode, the band token
   format, the canonical decimal rendering.
9. Timestamp detection or rendering in ``timestamps.py``: which patterns are
   recognized, the relative-time placeholder, the redaction placeholder, the
   canonical quantized format, the assumed timezone for naive inputs.
10. The stage order in ``pipeline.normalize``.
11. Any marker tuple in ``detect.py`` -- ``BOT_WALL_STRONG_MARKERS``,
    ``BOT_WALL_WEAK_MARKERS``, ``CHALLENGE_SERVER_TOKENS``,
    ``SOFT_ERROR_PHRASES`` -- or any of the thresholds beside them.  Adding a
    single marker flips some page from "returns an Evidence" to "raises
    ``BotWallDetected``".  That is the asymmetric, silent divergence R6 exists
    to prevent, and it is the reason those lists are module constants covered
    by this version rather than a set someone extends at call time.

Do **not** bump for: docstring edits, refactors provably output-identical,
new tests, new ``Policy`` fields (those already change ``policy_id`` on their
own via :meth:`~webanchor.Policy.to_dict`), or performance work.

Bumping invalidates every previously anchored fingerprint.  That is the point:
an invalidated fingerprint is a loud failure, and a loud failure is the entire
product.

Every bucketing scheme leaves residual divergence
=================================================

This section is the single, general statement of a property that WebAnchor's
three bucketing schemes all share.  It lives here, once, and the modules that
implement those schemes cross-reference it rather than restating it -- three
copies of a caveat drift, and a drifted caveat is worse than none.

The schemes are:

* **Timestamp quantization** (:mod:`webanchor.timestamps`, ``timestamp_mode``
  ``"quantize"``) -- flooring an instant to ``timestamp_quantum_seconds``.
* **Significant-digit rounding** (:mod:`webanchor.numbers`,
  ``number_band_mode`` ``"significant"``) -- snapping a value onto the lattice
  of numbers with ``number_significant_digits`` significant digits.
* **Grid banding** (:mod:`webanchor.numbers`, ``number_band_mode``
  ``"grid"``) -- flooring a value onto a lattice of width
  ``number_grid_step`` and printing the bucket.

All three work the same way: partition a continuum into buckets, and emit the
bucket instead of the reading.  Two validators whose readings fall in one
bucket agree exactly.  Two validators whose readings **straddle a bucket
edge** disagree exactly as completely as if there had been no bucketing at
all -- the outputs are different strings, ``strict_eq`` compares strings, and
the round fails.  Widening the bucket does not change the shape of this; it
only changes how often an edge falls between two readings::

    residual divergence risk  ~=  spread / bucket_width

where *spread* is the range of readings across the validator set for the value
in question (the wall-clock window between fetches, for timestamps; the drift
of the underlying number, for prices and counters), and *bucket_width* is the
quantum, the significant-digit step at that magnitude, or the grid step.  The
approximation holds while ``spread << bucket_width``; once the spread
approaches the bucket width, disagreement is the normal case.

Three consequences worth stating plainly:

1. **The risk never reaches zero.** No finite bucket width eliminates it. A
   quantum of a year still has 365 edges' worth of pages that straddle one.
2. **It compounds across values.** ``n`` independent bucketed values on one
   page multiply the per-value survival probability, so a page with ten
   timestamps fails roughly ten times as often as a page with one.
3. **The failures are correlated, not independent.** Validators fetch in a
   burst. A burst that happens to sit on a bucket edge splits the validator
   set, which is the worst case rather than the average one -- the naive
   per-validator probability understates it.

**Redaction is the only total guarantee.** ``timestamp_mode="redact"``
replaces the instant with ``[timestamp]``, a string that does not depend on
when anyone fetched, so its residual risk is exactly zero rather than merely
small. There is no numeric equivalent shipped today because a redacted price
carries no evidence; if you need a zero-risk numeric path, the answer is to
not put the volatile number in the anchored text at all.

Choose accordingly: bucket when the value is the evidence and you can size the
bucket against your spread, redact when it is page furniture. Paying a
permanent consensus risk for furniture is a bad trade, which is why
``redact`` is the default timestamp mode and ``none`` the default number mode.
"""

__all__ = ["BEHAVIOR_VERSION"]

#: Version of the output-affecting behavior of the normalization pipeline.
#: See the module docstring for the bump checklist.
#:
#: History
#: -------
#: 1. The normalization stages: stripping, text canonicalization, timestamps, numbers.
#: 2. Fetch/detection: the ``detect.py`` marker tables entered the output-affecting set
#:    (item 11 above), and number banding's ``percent`` mode was replaced by
#:    ``grid``.  Both change what the library does with some input, and the
#:    detection tables are not reachable from any :class:`~webanchor.Policy`
#:    field, so this constant is the only thing that can carry them into
#:    ``policy_id``.  Every fingerprint anchored under version 1 is
#:    invalidated, loudly, which is the designed behavior.
BEHAVIOR_VERSION = 2
