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


class _PriorSpyRetriever:
    """Captures the retriever call so tests can assert on query/intent/prior_intent."""

    def __init__(self):
        self.captured: dict = {}

    async def retrieve(self, query, intent, top_k=3, *, prior_intent=""):
        self.captured["query"] = query
        self.captured["intent"] = intent
        self.captured["prior_intent"] = prior_intent
        return [
            RetrievedChunk(policy_id="policy_101", title="t", content="c", score=1.0)
        ]


def _make_distill_pipeline(spy):
    pipeline = EmailPipeline()

    class _FakeDistiller:
        async def distill(self, subject, body, *, transcript=None):
            return DistillResult(
                queries=["paper page limit", "appendix placement policy"],
                intent="submission_format_policy",
                confidence=0.9,
            )

    pipeline.distiller = _FakeDistiller()
    pipeline.retriever = spy
    return pipeline


async def test_distill_mode_prior_intent_empty_by_default_e001_guard(
    session, monkeypatch
):
    """E001 guard + B7 gate: in distill mode the classified intent never enters
    the query text, and by default (``INTENT_PRIOR_ENABLED=False``) it does not
    reach the retriever's ``prior_intent`` boost channel either.

    Historically (E001/E003) folding the intent token into the query hurt
    retrieval, so distill mode passes ``retrieval_intent=""``. B6's E010 ablation
    then showed B5's ``prior_intent`` boost itself badly regresses fusion
    retrieval (hit@1 .730→.243), so B7 gates it behind ``INTENT_PRIOR_ENABLED``
    (default off): production must forward ``prior_intent=""`` regardless of
    what the classifier/distiller found.
    """
    monkeypatch.setattr(settings, "QUERY_STRATEGY", "distill")
    assert settings.INTENT_PRIOR_ENABLED is False  # default

    spy = _PriorSpyRetriever()
    pipeline = _make_distill_pipeline(spy)

    result = await pipeline.process_email(
        {"from": "a@b.com", "subject": "Formatting", "body": "How many pages allowed?"},
        session,
    )

    # Query = distilled queries joined, byte-identical — NO intent token appended.
    assert spy.captured["query"] == "paper page limit appendix placement policy"
    assert "submission_format_policy" not in spy.captured["query"]
    # The query-shaping ``intent`` arg stays empty in distill mode (E001 protection).
    assert spy.captured["intent"] == ""
    # B7: prior gate is off by default → the boost channel gets "", not the
    # classified intent.
    assert spy.captured["prior_intent"] == ""

    # Persisted context mirrors this: empty query-intent, empty prior, stable hash.
    ctx = (
        await EmailRepository().get_email_by_id(session, result.email_id)
    ).retrieval_context
    assert ctx["intent"] == ""
    assert ctx["prior_intent"] == ""
    assert isinstance(ctx["chunk_hash"], str) and len(ctx["chunk_hash"]) == 40


async def test_distill_mode_prior_intent_flows_when_flag_enabled_e001_guard(
    session, monkeypatch
):
    """B7 opt-in path: with ``INTENT_PRIOR_ENABLED=True``, the classified intent
    flows through to the retriever's ``prior_intent`` boost channel again — but
    the E001 guard (query text unchanged, no intent token) must still hold.
    """
    monkeypatch.setattr(settings, "QUERY_STRATEGY", "distill")
    monkeypatch.setattr(settings, "INTENT_PRIOR_ENABLED", True)

    spy = _PriorSpyRetriever()
    pipeline = _make_distill_pipeline(spy)

    result = await pipeline.process_email(
        {"from": "a@b.com", "subject": "Formatting", "body": "How many pages allowed?"},
        session,
    )

    # E001 guard still holds: query byte-identical, no intent token appended.
    assert spy.captured["query"] == "paper page limit appendix placement policy"
    assert "submission_format_policy" not in spy.captured["query"]
    assert spy.captured["intent"] == ""
    # The classified intent now rides the separate prior_intent (boost) channel.
    assert spy.captured["prior_intent"] == "submission_format_policy"

    ctx = (
        await EmailRepository().get_email_by_id(session, result.email_id)
    ).retrieval_context
    assert ctx["intent"] == ""
    assert ctx["prior_intent"] == "submission_format_policy"
    assert isinstance(ctx["chunk_hash"], str) and len(ctx["chunk_hash"]) == 40
