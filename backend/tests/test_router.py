"""Unit tests for the EmailRouter two-lane decision (no DB, no API)."""

from app.pipeline.classifier import ClassificationResult
from app.pipeline.router import EmailRouter


def _classification(intent: str, confidence: float) -> ClassificationResult:
    return ClassificationResult(
        intent=intent, confidence=confidence, reasoning="test", secondary_intents=[]
    )


def test_ethics_always_human_review(sample_retrieved_chunk) -> None:
    router = EmailRouter()
    decision = router.route(
        _classification("ethics_concern", 0.99), [sample_retrieved_chunk]
    )
    assert decision.lane == "human_review"
    assert decision.override_reason is not None


def test_authorship_always_human_review(sample_retrieved_chunk) -> None:
    router = EmailRouter()
    decision = router.route(
        _classification("authorship_dispute", 0.99), [sample_retrieved_chunk]
    )
    assert decision.lane == "human_review"


def test_review_assignment_always_human_review(sample_retrieved_chunk) -> None:
    router = EmailRouter()
    decision = router.route(
        _classification("review_assignment", 0.99), [sample_retrieved_chunk]
    )
    assert decision.lane == "human_review"


def test_faq_high_confidence_routes_faq(sample_retrieved_chunk) -> None:
    router = EmailRouter()
    decision = router.route(
        _classification("submission_deadline", 0.85), [sample_retrieved_chunk]
    )
    assert decision.lane == "faq"


def test_faq_low_confidence_routes_human_review(sample_retrieved_chunk) -> None:
    router = EmailRouter()
    decision = router.route(
        _classification("submission_deadline", 0.40), [sample_retrieved_chunk]
    )
    assert decision.lane == "human_review"


def test_faq_no_chunks_routes_human_review() -> None:
    router = EmailRouter()
    decision = router.route(_classification("submission_deadline", 0.90), [])
    assert decision.lane == "human_review"
