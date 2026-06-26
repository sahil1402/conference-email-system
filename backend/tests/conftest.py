"""Shared pytest fixtures.

Tests run with no real database and no Anthropic API calls. ``mock_db_session``
stands in for an AsyncSession; the sample-data fixtures provide realistic,
self-contained inputs so each test stays independent.
"""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.pipeline.classifier import ClassificationResult
from app.pipeline.retriever import RetrievedChunk


@pytest.fixture
def mock_db_session() -> AsyncMock:
    """An AsyncMock standing in for an async SQLAlchemy session."""
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def sample_email_dict() -> dict:
    """A realistic inbound email dict (pipeline / orchestrator input shape)."""
    return {
        "from": "author@university.edu",
        "to": "chairs@conference.org",
        "subject": "Deadline question",
        "body": "When is the paper submission deadline this year?",
        "timestamp": "2025-10-15T09:23:00Z",
    }


@pytest.fixture
def sample_classification_result() -> ClassificationResult:
    """A high-confidence deadline classification."""
    return ClassificationResult(
        intent="submission_deadline",
        confidence=0.85,
        reasoning="Matched deadline keywords.",
        secondary_intents=[],
    )


@pytest.fixture
def sample_retrieved_chunk() -> RetrievedChunk:
    """A single retrieved policy chunk with a positive relevance score."""
    return RetrievedChunk(
        policy_id="policy_002",
        title="Full Paper Submission Deadline",
        content="The full paper deadline is specified in Anywhere on Earth time.",
        score=5.0,
        category="submission_deadlines",
        tags=["deadline", "full-paper", "aoe"],
    )
