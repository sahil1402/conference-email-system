"""Tests for the BM25 PolicyRetriever reading its corpus from the DB.

Mirrors the FAISS retriever's DB-backed pattern: a short-lived async session
via an injectable ``session_factory``, filtered through
``PolicyRepository.list_for_index`` (active rows in public/internal
visibility only).
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, PolicyDocument
from app.pipeline.retriever import PolicyRetriever
from app.pipeline.faiss_retriever import FAISSRetriever


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
            PolicyDocument(policy_key="policy_101", title="Submission deadline",
                           content="Papers must be submitted by the deadline.",
                           visibility="public", status="active"),
            PolicyDocument(policy_key="int_ext", title="Deadline extension",
                           content="The submission deadline has been extended.",
                           visibility="internal", status="active"),
            PolicyDocument(policy_key="policy_102", title="Retired rule",
                           content="deadline old removed", visibility="public", status="inactive"),
        ])
        await s.commit()
    yield f
    await engine.dispose()


async def test_bm25_retrieves_active_public_and_internal_excludes_inactive(factory):
    r = PolicyRetriever(session_factory=factory)
    hits = await r.retrieve("submission deadline extended", intent="submission_requirements", top_k=5)
    keys = {h.policy_id for h in hits}
    assert "int_ext" in keys           # internal is retrievable
    assert "policy_102" not in keys    # inactive is excluded


@pytest.mark.ml
async def test_faiss_excludes_inactive(factory):
    r = FAISSRetriever(session_factory=factory)
    await r.build()
    keys = {d["policy_id"] for d in r._docs}
    assert "int_ext" in keys
    assert "policy_102" not in keys   # inactive excluded via list_for_index
