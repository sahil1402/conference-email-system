"""Unit tests for the BM25 PolicyRetriever (DB-backed via list_for_index).

Mirrors the FAISS retriever's DB-backed pattern: an in-memory SQLite engine
(StaticPool) seeded with a handful of active, public ``PolicyDocument`` rows,
wired in through the retriever's injectable ``session_factory`` so no real
database or file-backed corpus is touched.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, PolicyDocument
from app.pipeline.retriever import PolicyRetriever


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with f() as s:
        s.add_all([
            PolicyDocument(
                policy_key="policy_101",
                title="Paper Submission Deadline",
                content=(
                    "Papers must be submitted by the paper submission deadline "
                    "for the conference."
                ),
                category="submission_deadlines",
                tags=["deadline", "submission"],
                visibility="public",
                status="active",
            ),
            PolicyDocument(
                policy_key="policy_102",
                title="Formatting Requirements",
                content=(
                    "All papers must follow the formatting requirements, "
                    "including the strict page limit."
                ),
                category="formatting_requirements",
                tags=["formatting", "page-limit"],
                visibility="public",
                status="active",
            ),
            PolicyDocument(
                policy_key="policy_103",
                title="Registration and Attendance",
                content=(
                    "At least one author of an accepted paper must register "
                    "and pay the conference fee to present."
                ),
                category="general_faq",
                tags=["registration"],
                visibility="public",
                status="active",
            ),
        ])
        await s.commit()
    yield f
    await engine.dispose()


@pytest.fixture
def retriever(factory) -> PolicyRetriever:
    return PolicyRetriever(session_factory=factory)


async def test_deadline_query_returns_results(retriever: PolicyRetriever) -> None:
    results = await retriever.retrieve(
        "paper submission deadline", "submission_deadline"
    )
    assert len(results) > 0
    assert any(r.policy_id == "policy_101" for r in results)


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
