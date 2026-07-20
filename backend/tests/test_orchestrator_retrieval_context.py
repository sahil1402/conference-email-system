"""process_email persists the retrieval context used to ground the draft."""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.models import Base
from app.pipeline.distiller import DistillResult
from app.pipeline.orchestrator import EmailPipeline
from app.pipeline.retriever import RetrievedChunk
from app.repositories.email_repository import EmailRepository


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_process_email_persists_retrieval_context(session):
    pipeline = EmailPipeline()
    result = await pipeline.process_email(
        {
            "from": "author@uni.edu",
            "to": "chair@conf.org",
            "subject": "Question about the submission deadline",
            "body": "Can I get an extension on the paper submission deadline?",
            "timestamp": "",
        },
        session,
    )

    email = await EmailRepository().get_email_by_id(session, result.email_id)
    ctx = email.retrieval_context
    assert ctx is not None
    assert isinstance(ctx["query"], str) and ctx["query"]
    assert "intent" in ctx
    # The stored ids are exactly the top-k that grounded this draft (both derive
    # from the same retrieval call, so they match even when the set is empty).
    assert ctx["retrieved_ids"] == [c.policy_id for c in result.retrieved_chunks]


async def test_distill_mode_prior_intent_never_enters_query_e001_guard(
    session, monkeypatch
):
    """E001 guard: in distill mode the classified intent feeds the soft prior
    (a SEPARATE ``prior_intent`` channel), never the query text.

    Historically (E001/E003) folding the intent token into the query hurt
    retrieval, so distill mode passes ``retrieval_intent=""``. B5 must keep that
    invariant: the query string handed to the retriever stays byte-identical to the
    distilled queries, and the intent reaches only the boost channel.
    """
    monkeypatch.setattr(settings, "QUERY_STRATEGY", "distill")

    pipeline = EmailPipeline()

    class _FakeDistiller:
        async def distill(self, subject, body):
            return DistillResult(
                queries=["paper page limit", "appendix placement policy"],
                intent="submission_format_policy",
                confidence=0.9,
            )

    pipeline.distiller = _FakeDistiller()

    captured: dict = {}

    class _SpyRetriever:
        async def retrieve(self, query, intent, top_k=3, *, prior_intent=""):
            captured["query"] = query
            captured["intent"] = intent
            captured["prior_intent"] = prior_intent
            return [
                RetrievedChunk(policy_id="policy_101", title="t", content="c", score=1.0)
            ]

    pipeline.retriever = _SpyRetriever()

    result = await pipeline.process_email(
        {"from": "a@b.com", "subject": "Formatting", "body": "How many pages allowed?"},
        session,
    )

    # Query = distilled queries joined, byte-identical — NO intent token appended.
    assert captured["query"] == "paper page limit appendix placement policy"
    assert "submission_format_policy" not in captured["query"]
    # The query-shaping ``intent`` arg stays empty in distill mode (E001 protection);
    # the classified intent rides ONLY the separate prior_intent (boost) channel.
    assert captured["intent"] == ""
    assert captured["prior_intent"] == "submission_format_policy"

    # Persisted context mirrors this: empty query-intent, prior recorded, stable hash.
    ctx = (
        await EmailRepository().get_email_by_id(session, result.email_id)
    ).retrieval_context
    assert ctx["intent"] == ""
    assert ctx["prior_intent"] == "submission_format_policy"
    assert isinstance(ctx["chunk_hash"], str) and len(ctx["chunk_hash"]) == 40
