"""Unit tests for the EmailRouter two-lane decision (no DB, no API).

The FAQ lane is now a property of the generated *draft* (completeness,
grounding, and the drafter's self-rated answer confidence), not the email's
classified intent. ``SENSITIVE_INTENTS`` is kept as an (empty) seam — see
router.py.
"""

from app.pipeline.classifier import ClassificationResult
from app.pipeline.drafter import DraftResponse
from app.pipeline.router import (
    LANE_FAQ,
    LANE_HUMAN_REVIEW,
    EmailRouter,
    RoutingDecision,
    apply_self_sufficiency_floor,
)


def _draft(placeholders=None, notes=None, citations=("policy_101",), conf=0.95):
    return DraftResponse(
        draft_text="ok",
        notes_for_chair=notes,
        placeholders=list(placeholders or []),
        citations=list(citations),
        model_used="m",
        answer_confidence=conf,
    )


def _clf(intent="submission_requirements", confidence=0.9):
    return ClassificationResult(
        intent=intent, confidence=confidence, reasoning="t", method="test"
    )


def test_complete_grounded_confident_draft_is_faq():
    r = EmailRouter().route(_clf(), ["c"], _draft())
    assert r.lane == "faq"


def test_placeholder_forces_human():
    r = EmailRouter().route(_clf(), ["c"], _draft(placeholders=["date"]))
    assert r.lane == "human_review"


def test_notes_force_human():
    r = EmailRouter().route(_clf(), ["c"], _draft(notes="verify X"))
    assert r.lane == "human_review"


def test_ungrounded_forces_human():
    r = EmailRouter().route(_clf(), [], _draft(citations=()))
    assert r.lane == "human_review"


def test_low_answer_confidence_forces_human():
    r = EmailRouter().route(_clf(), ["c"], _draft(conf=0.4))
    assert r.lane == "human_review"


def test_none_answer_confidence_forces_human():
    r = EmailRouter().route(_clf(), ["c"], _draft(conf=None))
    assert r.lane == "human_review"


def test_appeal_intent_can_be_faq_when_draft_complete():
    # appeals are no longer hard-blocked (SENSITIVE_INTENTS emptied)
    r = EmailRouter().route(_clf(intent="desk_reject_appeal"), ["c"], _draft())
    assert r.lane == "faq"


def _faq_routing():
    return RoutingDecision(
        lane=LANE_FAQ, reason="stub faq", confidence_used=0.9, threshold_applied=0.65
    )


def test_apply_self_sufficiency_floor_demotes_faq_with_placeholders():
    routing = apply_self_sufficiency_floor(_faq_routing(), _draft(placeholders=["date"]))
    assert routing.lane == LANE_HUMAN_REVIEW
    assert routing.override_reason == (
        "draft is not self-sufficient (1 placeholder(s), notes=no) — requires a human"
    )


def test_apply_self_sufficiency_floor_demotes_faq_with_notes():
    routing = apply_self_sufficiency_floor(_faq_routing(), _draft(notes="verify X"))
    assert routing.lane == LANE_HUMAN_REVIEW
    assert routing.override_reason == (
        "draft is not self-sufficient (0 placeholder(s), notes=yes) — requires a human"
    )


def test_apply_self_sufficiency_floor_is_noop_on_self_sufficient_draft():
    routing = _faq_routing()
    result = apply_self_sufficiency_floor(routing, _draft())
    assert result.lane == LANE_FAQ
    assert result.override_reason is None
    assert result == routing
