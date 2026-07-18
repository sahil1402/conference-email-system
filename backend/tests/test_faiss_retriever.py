"""Tests for the FAISS dense-vector retriever and the retrieval-info endpoint.

No real database is touched: ``policy_repository`` is replaced with a fake that
returns 5 PolicyDocument-like objects, and the session factory is a no-op async
context manager. The embedder (all-MiniLM-L6-v2) is loaded from the local
huggingface cache.

Note on the result contract: this project's ``RetrievedChunk`` exposes
``policy_id / title / content / score / category / tags`` (kept identical to the
BM25 retriever so the backends are swappable). Assertions therefore target those
fields — ``content`` is the chunk text and ``policy_id`` its identifier.
"""

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

import main
import app.pipeline.retriever as retriever_module
from app.pipeline.faiss_retriever import FAISSRetriever
from app.pipeline.retriever import PolicyRetriever, RetrievedChunk

# Heavy ML module (embedding model loads/training) — deselected by -m 'not ml'.
pytestmark = pytest.mark.ml


def _fake_policies() -> list:
    """5 realistic conference-policy documents (PolicyDocument-shaped)."""
    return [
        SimpleNamespace(
            policy_key="policy_001",
            title="Paper Submission Deadline",
            content=(
                "The full paper submission deadline is fixed in Anywhere on Earth "
                "(AoE) time. Late submissions are not accepted and extensions are rare."
            ),
            category="submission_deadlines",
        ),
        SimpleNamespace(
            policy_key="policy_002",
            title="Formatting Requirements",
            content=(
                "All papers must use the official LaTeX template, respect the strict "
                "page limit, and follow the two-column format with anonymized authors."
            ),
            category="formatting_requirements",
        ),
        SimpleNamespace(
            policy_key="policy_003",
            title="Ethics and Plagiarism Policy",
            content=(
                "Plagiarism, dual submission, and undisclosed conflicts of interest "
                "are serious ethics violations subject to investigation and sanctions."
            ),
            category="ethics_policy",
        ),
        SimpleNamespace(
            policy_key="policy_004",
            title="Submission Withdrawal",
            content=(
                "Authors may withdraw a submission before the notification date "
                "through the submission portal; withdrawal after acceptance is restricted."
            ),
            category="withdrawal_policy",
        ),
        SimpleNamespace(
            policy_key="policy_005",
            title="Registration and Attendance",
            content=(
                "At least one author of an accepted paper must register and pay the "
                "conference fee in order to present, either virtually or in person."
            ),
            category="general_faq",
        ),
    ]


class _FakePolicyRepo:
    """Stands in for PolicyRepository — ignores the session, returns fakes."""

    async def get_all_policies(self, db) -> list:
        return _fake_policies()

    async def list_for_index(self, db, visibilities=("public", "internal")) -> list:
        return _fake_policies()


class _DummySession:
    """No-op async context manager used as the session factory."""

    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def faiss_retriever() -> FAISSRetriever:
    """A FAISSRetriever wired to the fake repo and a no-op session."""
    return FAISSRetriever(
        policy_repo=_FakePolicyRepo(),
        session_factory=lambda: _DummySession(),
    )


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Retriever behavior
# ---------------------------------------------------------------------------
async def test_faiss_retriever_returns_results(faiss_retriever):
    results = await faiss_retriever.retrieve(
        "paper submission deadline", "submission_deadline", top_k=3
    )
    assert len(results) >= 1
    assert all(r.score > 0.0 for r in results)
    # content == chunk text; policy_id == source identifier.
    assert all(r.content.strip() for r in results)
    assert all(r.policy_id.strip() for r in results)
    # The deadline query should surface the deadline policy as the top hit.
    assert results[0].policy_id == "policy_001"


async def test_faiss_retriever_top_k_respected(faiss_retriever):
    results = await faiss_retriever.retrieve("formatting", "formatting_requirements", top_k=2)
    assert len(results) <= 2


async def test_faiss_retriever_result_type(faiss_retriever):
    results = await faiss_retriever.retrieve("ethics plagiarism", "ethics_concern", top_k=3)
    assert results, "expected at least one result"
    for r in results:
        assert isinstance(r, RetrievedChunk)
        # All contract fields are present on each chunk.
        assert r.policy_id is not None
        assert r.title is not None
        assert r.content is not None
        assert isinstance(r.score, float)
        assert r.category is not None


async def test_faiss_index_rebuild(faiss_retriever):
    await faiss_retriever.retrieve("deadline", "submission_deadline", top_k=2)
    await faiss_retriever.rebuild_index()  # must not raise
    results = await faiss_retriever.retrieve(
        "registration fee to present", "general_inquiry", top_k=2
    )
    assert len(results) >= 1


async def test_retrieval_info_endpoint(client):
    resp = await client.get("/api/v1/retrieval/info")
    assert resp.status_code == 200
    body = resp.json()
    assert "backend" in body
    assert "document_count" in body


def test_bm25_still_works_after_faiss_added(monkeypatch):
    # Force the BM25 backend and reset the factory singleton for a clean build.
    monkeypatch.setattr(retriever_module.settings, "RETRIEVAL_BACKEND", "bm25")
    monkeypatch.setattr(retriever_module, "_retriever_singleton", None)
    monkeypatch.setattr(retriever_module, "_retriever_backend", None)

    retriever = retriever_module.get_retriever()
    assert isinstance(retriever, PolicyRetriever)
    assert not isinstance(retriever, FAISSRetriever)
