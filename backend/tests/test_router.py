"""Unit tests for the EmailRouter two-lane decision (no DB, no API).

The FAQ lane is now a property of the generated *draft* (completeness,
grounding, and the drafter's self-rated answer confidence), not the email's
classified intent. ``FAQ_ELIGIBLE_INTENTS`` is retired; ``SENSITIVE_INTENTS``
is kept as an (empty) seam — see router.py.
"""

from app.pipeline.classifier import ClassificationResult
from app.pipeline.drafter import DraftResponse
from app.pipeline.router import EmailRouter


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
