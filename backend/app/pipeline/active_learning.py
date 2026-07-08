"""Active-learning candidate flagging (Phase 5G).

Two DISTINCT signals mark an email as worth a future human labeling pass. They
mean different things, so they are flagged separately (never conflated):

1. **Low confidence** — a chair approved/correctly-routed an email whose
   router-used confidence sat just below the FAQ threshold (a near-miss the
   human effectively rescued). Band: ``[threshold - margin, threshold)``.
2. **Meaningful edit** — a chair substantially rewrote the draft before sending
   (word-level change ratio above a floor), as opposed to a typo-level fix.

This module only DECIDES what to flag; it writes nothing. Callers (the
approve/reroute path) turn the returned events into audit-log entries. This
flags candidates for future labeling only — it never triggers retraining.

Confidence seam (Phase 5B): the router compares the CALIBRATED confidence when
one is present, else the raw score. ``_used_confidence`` mirrors that exactly so
the flag reflects the value the routing decision actually used.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from app.core.config import settings

# Distinct audit action types (kept separate so the candidates endpoint can
# query each signal independently and the two are never conflated).
FLAG_LOW_CONFIDENCE = "flagged_low_confidence"
FLAG_MEANINGFUL_EDIT = "flagged_meaningful_edit"


def _get(obj: Any, key: str) -> Any:
    """Read ``key`` from a dict or an attribute-bearing object (or None)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _used_confidence(classification_result: Any) -> float | None:
    """The confidence the router actually compared to the threshold.

    Mirrors the Phase 5B router seam: prefer ``calibrated_confidence`` when set,
    otherwise the raw ``confidence``. Accepts either the persisted classification
    dict or a ``ClassificationResult`` object.
    """
    calibrated = _get(classification_result, "calibrated_confidence")
    if calibrated is not None:
        return float(calibrated)
    raw = _get(classification_result, "confidence")
    return float(raw) if raw is not None else None


def should_flag_low_confidence(
    classification_result: Any, threshold_margin: float | None = None
) -> bool:
    """Flag near-miss confidence just below the FAQ threshold.

    ``threshold_margin`` defaults to ``settings.AL_CONFIDENCE_MARGIN`` (0.15).
    Returns True only when ``threshold - margin <= used_confidence < threshold``:
    a comfortably-above-threshold email is not flagged, and one far below the
    threshold is not a "lucky near-miss" either.
    """
    margin = settings.AL_CONFIDENCE_MARGIN if threshold_margin is None else threshold_margin
    confidence = _used_confidence(classification_result)
    if confidence is None:
        return False
    threshold = settings.FAQ_CONFIDENCE_THRESHOLD
    return (threshold - margin) <= confidence < threshold


def edit_change_ratio(original_text: str, edited_text: str) -> float:
    """Word-level dissimilarity in [0, 1] between two drafts.

    ``1 - SequenceMatcher.ratio()`` over word tokens (stdlib difflib; equivalent
    to an LCS-based word diff, matching 5F's front-end diff conceptually). 0.0 =
    identical, ~1.0 = fully rewritten.
    """
    a = (original_text or "").split()
    b = (edited_text or "").split()
    if not a and not b:
        return 0.0
    return 1.0 - SequenceMatcher(None, a, b).ratio()


def should_flag_meaningful_edit(
    original_text: str, edited_text: str, min_change_ratio: float | None = None
) -> bool:
    """Flag when the chair's edit changed more than ``min_change_ratio`` of words.

    ``min_change_ratio`` defaults to ``settings.AL_EDIT_RATIO`` (0.15). A single
    typo fix stays below the floor; a substantial rewrite exceeds it.
    """
    ratio = settings.AL_EDIT_RATIO if min_change_ratio is None else min_change_ratio
    return edit_change_ratio(original_text, edited_text) > ratio


def build_flag_events(
    classification_result: Any,
    *,
    was_edited: bool = False,
    original_text: str = "",
    edited_text: str = "",
) -> list[tuple[str, dict]]:
    """Return ``(audit_action, details)`` tuples for whichever signals fired.

    Both can co-occur on one email (returned as two separate events — never
    merged). Meaningful-edit is only considered when ``was_edited`` is True.
    """
    events: list[tuple[str, dict]] = []

    if should_flag_low_confidence(classification_result):
        confidence = _used_confidence(classification_result)
        events.append(
            (
                FLAG_LOW_CONFIDENCE,
                {
                    "reason": "low_confidence",
                    "confidence_used": round(confidence, 4) if confidence is not None else None,
                    "threshold": settings.FAQ_CONFIDENCE_THRESHOLD,
                    "margin": settings.AL_CONFIDENCE_MARGIN,
                },
            )
        )

    if was_edited and should_flag_meaningful_edit(original_text, edited_text):
        events.append(
            (
                FLAG_MEANINGFUL_EDIT,
                {
                    "reason": "meaningful_edit",
                    "change_ratio": round(edit_change_ratio(original_text, edited_text), 4),
                    "min_ratio": settings.AL_EDIT_RATIO,
                },
            )
        )

    return events
