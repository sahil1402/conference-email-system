"""Unit tests for the BM25 PolicyRetriever (reads the KB file, no DB, no API)."""

import pytest

from app.pipeline.retriever import PolicyRetriever


@pytest.fixture
def retriever() -> PolicyRetriever:
    return PolicyRetriever()


async def test_deadline_query_returns_results(retriever: PolicyRetriever) -> None:
    results = await retriever.retrieve(
        "paper submission deadline", "submission_deadline"
    )
    assert len(results) > 0


async def test_results_have_non_negative_scores(retriever: PolicyRetriever) -> None:
    results = await retriever.retrieve(
        "paper submission deadline", "submission_deadline"
    )
    assert all(r.score >= 0 for r in results)


async def test_top_k_respected(retriever: PolicyRetriever) -> None:
    results = await retriever.retrieve(
        "formatting page limit", "formatting_requirements", top_k=2
    )
    assert len(results) <= 2


async def test_rebuild_index_does_not_crash(retriever: PolicyRetriever) -> None:
    retriever.rebuild_index()
    results = await retriever.retrieve("deadline", "submission_deadline")
    assert len(results) > 0
