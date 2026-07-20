"""Unit tests for the EmailRouter two-lane decision (no DB, no API)."""

from app.pipeline.classifier import ClassificationResult
from app.pipeline.router import EmailRouter


def _classification(intent: str, confidence: float) -> ClassificationResult:
    return ClassificationResult(
        intent=intent, confidence=confidence, reasoning="test", secondary_intents=[]
    )


# Sensitive set (14-intent taxonomy, appeals_integrity family):
# {review_decision_appeal, desk_reject_appeal, anonymity_violation}. Old sensitive
# set was {authorship_dispute, ethics_concern, review_assignment} — none of those
# are sensitive under the new taxonomy, so each test below now exercises one of
# the three current sensitive intents, preserving the "sensitive → always human
# review" purpose.


def test_review_decision_appeal_always_human_review(sample_retrieved_chunk) -> None:
    router = EmailRouter()
    decision = router.route(
        _classification("review_decision_appeal", 0.99), [sample_retrieved_chunk]
    )
    assert decision.lane == "human_review"
    assert decision.override_reason is not None


def test_desk_reject_appeal_always_human_review(sample_retrieved_chunk) -> None:
    router = EmailRouter()
    decision = router.route(
        _classification("desk_reject_appeal", 0.99), [sample_retrieved_chunk]
    )
    assert decision.lane == "human_review"


def test_anonymity_violation_always_human_review(sample_retrieved_chunk) -> None:
    router = EmailRouter()
    decision = router.route(
        _classification("anonymity_violation", 0.99), [sample_retrieved_chunk]
    )
    assert decision.lane == "human_review"


# TODO(B4): FAQ_ELIGIBLE_INTENTS is interim-empty under the one-chair MVP (see
# router.py) — Task B4 derives the real list from KB coverage. Until then every
# intent, however confident/grounded, escalates to human_review. These tests
# assert today's actual (interim) behavior; once B4 populates the list, restore
# an assertion of `lane == "faq"` for a genuinely FAQ-eligible intent.
def test_faq_high_confidence_routes_faq(sample_retrieved_chunk) -> None:
    router = EmailRouter()
    decision = router.route(
        _classification("submission_requirements", 0.85), [sample_retrieved_chunk]
    )
    assert decision.lane == "human_review"


# TODO(B4): see note above — FAQ_ELIGIBLE_INTENTS is empty until Task B4.
def test_faq_low_confidence_routes_human_review(sample_retrieved_chunk) -> None:
    router = EmailRouter()
    decision = router.route(
        _classification("submission_requirements", 0.40), [sample_retrieved_chunk]
    )
    assert decision.lane == "human_review"


# TODO(B4): see note above — FAQ_ELIGIBLE_INTENTS is empty until Task B4.
def test_faq_no_chunks_routes_human_review() -> None:
    router = EmailRouter()
    decision = router.route(_classification("submission_requirements", 0.90), [])
    assert decision.lane == "human_review"
