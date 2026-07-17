"""Tests for the query distiller (E003) and its orchestrator wiring.

All HTTP is mocked — no real model calls. The distiller must never raise:
every failure path returns None, and the orchestrator then falls back to the
keyword classifier plus a subject+body[:600] query.
"""

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core import tracing
from app.core.config import settings
from app.core.tracing import configure_tracing
from app.db.database import Base
from app.pipeline import distiller as distiller_module
from app.pipeline.distiller import DistillResult, EmailDistiller, _parse
from app.pipeline.orchestrator import EmailPipeline

_STRUCTURED = (
    "INTENT: authorship_dispute\n"
    "CONFIDENCE: 0.85\n"
    "QUERY: add co-author to author list after paper submission deadline\n"
    "QUERY: author list change procedure during review\n"
)


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------
def test_parse_structured_output():
    result = _parse(_STRUCTURED)
    assert result.intent == "authorship_dispute"
    assert result.confidence == 0.85
    assert result.queries == [
        "add co-author to author list after paper submission deadline",
        "author list change procedure during review",
    ]


def test_parse_unknown_intent_keeps_queries():
    result = _parse("INTENT: made_up_label\nCONFIDENCE: 0.9\nQUERY: some ask\n")
    assert result.intent is None  # keyword classifier will decide
    assert result.queries == ["some ask"]


def test_parse_without_queries_is_unusable():
    assert _parse("INTENT: general_inquiry\nCONFIDENCE: 0.7\n") is None


def test_parse_caps_queries_at_three():
    text = "INTENT: general_inquiry\nCONFIDENCE: 0.6\n" + "".join(
        f"QUERY: q{i}\n" for i in range(6)
    )
    assert _parse(text).queries == ["q0", "q1", "q2"]


# ---------------------------------------------------------------------------
# The distill call (mocked endpoint)
# ---------------------------------------------------------------------------
class _OkClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *args, **kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"choices": [{"message": {"content": _STRUCTURED}}]},
        )


class _RaisingClient(_OkClient):
    async def post(self, *args, **kwargs):
        raise httpx.ConnectError("connection refused")


async def test_distill_parses_model_output(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_PROVIDER", "local")
    monkeypatch.setattr(distiller_module.httpx, "AsyncClient", _OkClient)
    result = await EmailDistiller().distill("Co-author omission", "body text")
    assert result.intent == "authorship_dispute"
    assert len(result.queries) == 2


async def test_distill_connection_error_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_PROVIDER", "local")
    monkeypatch.setattr(distiller_module.httpx, "AsyncClient", _RaisingClient)
    assert await EmailDistiller().distill("s", "b") is None


async def test_distill_requires_local_provider(monkeypatch):
    # conftest already forces MODEL_PROVIDER="fallback"; no client must be
    # constructed at all.
    def _boom(*args, **kwargs):
        raise AssertionError("no HTTP client should be created")

    monkeypatch.setattr(distiller_module.httpx, "AsyncClient", _boom)
    assert await EmailDistiller().distill("s", "b") is None


# ---------------------------------------------------------------------------
# Orchestrator wiring (in-memory DB, stubbed distiller + capturing retriever)
# ---------------------------------------------------------------------------
class _CapturingRetriever:
    def __init__(self):
        self.calls: list[dict] = []

    async def retrieve(self, query, intent, top_k=3):
        self.calls.append({"query": query, "intent": intent})
        return []


class _StubDistiller:
    def __init__(self, result):
        self.result = result

    async def distill(self, subject, body):
        return self.result


@pytest_asyncio.fixture
async def db_factory(tmp_path):
    original_log_path = tracing._current_log_path
    configure_tracing(tmp_path / "trace.jsonl")
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()
    configure_tracing(original_log_path)


_EMAIL = {
    "from": "author@u.edu",
    "subject": "Co-author omission",
    "body": "We forgot to add a co-author to our submission. Can it be fixed?",
}


async def test_pipeline_uses_distilled_queries_and_intent(monkeypatch, db_factory):
    monkeypatch.setattr(settings, "QUERY_STRATEGY", "distill")
    pipeline = EmailPipeline()
    pipeline.distiller = _StubDistiller(
        DistillResult(
            queries=["add co-author after deadline", "author list change"],
            intent="authorship_dispute",
            confidence=0.9,
        )
    )
    pipeline.retriever = _CapturingRetriever()

    async with db_factory() as db:
        result = await pipeline.process_email(_EMAIL, db)

    assert result.classification.method == "llm_distiller"
    assert result.classification.intent == "authorship_dispute"
    assert result.classification.confidence == 0.9
    call = pipeline.retriever.calls[0]
    # Distilled lines joined into one query; NO intent token (E001/E003).
    assert call["query"] == "add co-author after deadline author list change"
    assert call["intent"] == ""


async def test_pipeline_falls_back_when_distillation_fails(monkeypatch, db_factory):
    monkeypatch.setattr(settings, "QUERY_STRATEGY", "distill")
    pipeline = EmailPipeline()
    pipeline.distiller = _StubDistiller(None)
    pipeline.retriever = _CapturingRetriever()

    async with db_factory() as db:
        result = await pipeline.process_email(_EMAIL, db)

    # Keyword classifier decides; query is the subject+body[:600] fallback.
    assert result.classification.method != "llm_distiller"
    call = pipeline.retriever.calls[0]
    assert call["query"] == f"{_EMAIL['subject']} {_EMAIL['body']}"
    assert call["intent"] == ""


async def test_pipeline_prefix_strategy_is_untouched(db_factory):
    # conftest pins QUERY_STRATEGY="prefix": the distiller must not run and
    # the legacy body[:300]+intent query must be preserved bit-for-bit.
    pipeline = EmailPipeline()
    pipeline.distiller = _StubDistiller(
        DistillResult(queries=["should never be used"], intent="ethics_concern")
    )
    pipeline.retriever = _CapturingRetriever()

    async with db_factory() as db:
        result = await pipeline.process_email(_EMAIL, db)

    assert result.classification.method != "llm_distiller"
    call = pipeline.retriever.calls[0]
    assert call["query"] == _EMAIL["body"][:300]
    assert call["intent"] == result.classification.intent
