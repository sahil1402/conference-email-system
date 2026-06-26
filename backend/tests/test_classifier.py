"""Unit tests for the keyword IntentClassifier (no DB, no API)."""

from app.pipeline.classifier import IntentClassifier


async def test_deadline_email_classified_correctly() -> None:
    classifier = IntentClassifier()
    result = await classifier.classify(
        "When is the paper submission deadline?", "Deadline"
    )
    assert result.intent == "submission_deadline"
    assert result.confidence > 0.3


async def test_ethics_email_classified_correctly() -> None:
    classifier = IntentClassifier()
    result = await classifier.classify(
        "I want to report a serious ethical violation by a reviewer",
        "Ethics concern",
    )
    assert result.intent == "ethics_concern"


async def test_formatting_email_classified_correctly() -> None:
    classifier = IntentClassifier()
    result = await classifier.classify(
        "What is the page limit for the main paper?", "Formatting question"
    )
    assert result.intent == "formatting_requirements"


async def test_ambiguous_email_has_lower_confidence() -> None:
    classifier = IntentClassifier()
    result = await classifier.classify(
        "I have a question about the deadline for the camera-ready formatting",
        "Question",
    )
    assert result.confidence < 0.85


async def test_unknown_email_falls_back_to_general_inquiry() -> None:
    classifier = IntentClassifier()
    result = await classifier.classify(
        "Hello, I have a general question about the conference", "Question"
    )
    assert result.intent == "general_inquiry"
    assert result.confidence <= 0.35


async def test_batch_classify_returns_correct_length() -> None:
    classifier = IntentClassifier()
    emails = [
        {"subject": "Deadline", "body": "When is the submission deadline?"},
        {"subject": "Formatting", "body": "What is the page limit and template?"},
        {"subject": "Ethics", "body": "I want to report plagiarism by an author."},
    ]
    results = await classifier.classify_batch(emails)
    assert len(results) == 3
