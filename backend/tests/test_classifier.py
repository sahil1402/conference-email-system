"""Unit tests for the keyword IntentClassifier (no DB, no API).

Intents here follow the 14-intent taxonomy (`app/pipeline/taxonomy.py`); see
docs/superpowers/sdd/task-A2-brief.md's old->new mapping table for the
provenance of each remapped case (purpose preserved, intent name updated).
"""

from app.pipeline.classifier import IntentClassifier
from app.pipeline.taxonomy import FALLBACK_INTENT


async def test_deadline_email_classified_correctly() -> None:
    # old: submission_deadline -> new: submission_requirements
    classifier = IntentClassifier()
    result = await classifier.classify(
        "When is the paper submission deadline, and am I still eligible "
        "given the due date?",
        "Deadline",
    )
    assert result.intent == "submission_requirements"
    assert result.confidence > 0.3


async def test_anonymity_violation_email_classified_correctly() -> None:
    # old: ethics_concern -> new: anonymity_violation (integrity report, not
    # an appeal, per the mapping table's ethics_concern note)
    classifier = IntentClassifier()
    result = await classifier.classify(
        "I want to report that a reviewer publicly posted identifying "
        "information that violates our double-blind anonymity.",
        "Integrity concern",
    )
    assert result.intent == "anonymity_violation"


async def test_formatting_email_classified_correctly() -> None:
    # old: formatting_requirements -> new: submission_format_policy
    classifier = IntentClassifier()
    result = await classifier.classify(
        "What is the page limit for the main paper?", "Formatting question"
    )
    assert result.intent == "submission_format_policy"


async def test_ambiguous_email_has_lower_confidence() -> None:
    classifier = IntentClassifier()
    result = await classifier.classify(
        "I have a question about the deadline for the camera-ready formatting",
        "Question",
    )
    assert result.confidence < 0.85


async def test_unknown_email_falls_back_to_cms_support() -> None:
    # old: general_inquiry -> new: FALLBACK_INTENT (cms_support) is now the
    # catch-all/fallback intent
    classifier = IntentClassifier()
    result = await classifier.classify(
        "Hello, I have a general question about the conference", "Question"
    )
    assert result.intent == FALLBACK_INTENT
    assert result.confidence <= 0.35


async def test_batch_classify_returns_correct_length() -> None:
    classifier = IntentClassifier()
    emails = [
        {"subject": "Deadline", "body": "When is the submission deadline?"},
        {"subject": "Formatting", "body": "What is the page limit and template?"},
        {
            "subject": "Anonymity",
            "body": "I want to report an anonymity violation by another author.",
        },
    ]
    results = await classifier.classify_batch(emails)
    assert len(results) == 3
