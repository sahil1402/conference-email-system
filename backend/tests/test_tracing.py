"""Tests for per-email pipeline tracing (Phase 5A).

Exercises the real orchestrator + trace endpoint end-to-end against a throwaway
in-memory SQLite DB (StaticPool keeps every connection on the same ``:memory:``
database) driven through httpx's ASGITransport. Tracing is redirected to a temp
JSONL file so the tests never touch the real backend/logs/ file. No Anthropic
API key is set, so the drafter takes its deterministic fallback path (no
network).
"""

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import main
from app.core import tracing
from app.core.tracing import PipelineTracer, configure_tracing, read_traces
from app.db.database import Base, get_db

_SAMPLE_EMAIL = {
    "from": "author@university.edu",
    "to": "chairs@conference.org",
    "subject": "When is the full paper submission deadline?",
    "body": (
        "Hello, could you confirm the exact full paper submission deadline and "
        "the timezone? I want to be sure I upload before the cutoff."
    ),
    "timestamp": "2026-07-01T09:00:00Z",
}

_EXPECTED_STAGES = ["classifier", "retriever", "router", "drafter"]


@pytest_asyncio.fixture
async def client(tmp_path):
    """In-memory DB + temp trace log + an httpx client wired to the app."""
    # Redirect tracing to a temp file for isolation; restore afterwards.
    original_log_path = tracing._current_log_path
    configure_tracing(tmp_path / "pipeline_trace.jsonl")

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_get_db():
        async with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    main.app.dependency_overrides.clear()
    await engine.dispose()
    configure_tracing(original_log_path)


async def test_all_four_stages_logged_for_one_email(client):
    """Ingesting an email records exactly the 4 pipeline stages, in order."""
    resp = await client.post("/api/v1/emails/ingest", json=_SAMPLE_EMAIL)
    assert resp.status_code == 200
    email_id = resp.json()["email_id"]

    entries = read_traces(email_id)
    assert [e["stage"] for e in entries] == _EXPECTED_STAGES
    # Every record carries the full trace schema and the right email id.
    for entry in entries:
        assert set(entry) == {
            "timestamp",
            "email_id",
            "stage",
            "input_summary",
            "output_summary",
            "duration_ms",
        }
        assert entry["email_id"] == email_id
        assert isinstance(entry["duration_ms"], (int, float))


async def test_stage_summaries_capture_expected_fields(client):
    """Each stage's input/output summary contains its documented fields."""
    resp = await client.post("/api/v1/emails/ingest", json=_SAMPLE_EMAIL)
    email_id = resp.json()["email_id"]
    by_stage = {e["stage"]: e for e in read_traces(email_id)}

    assert set(by_stage["classifier"]["output_summary"]) == {
        "intent",
        "confidence",
        "method",
    }
    assert set(by_stage["retriever"]["output_summary"]) >= {
        "chunk_ids",
        "scores",
        "backend",
    }
    assert set(by_stage["router"]["output_summary"]) == {"lane", "reason"}
    drafter_out = by_stage["drafter"]["output_summary"]
    assert set(drafter_out) == {"draft_length", "provider", "model_used"}
    # The trace records the draft length, never the draft text itself.
    assert isinstance(drafter_out["draft_length"], int)
    assert "draft_text" not in drafter_out


async def test_trace_endpoint_returns_ordered_entries(client):
    """GET /{id}/trace returns the stages in pipeline order for that email."""
    email_id = (await client.post("/api/v1/emails/ingest", json=_SAMPLE_EMAIL)).json()[
        "email_id"
    ]

    resp = await client.get(f"/api/v1/emails/{email_id}/trace")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email_id"] == email_id
    assert body["count"] == 4
    assert [e["stage"] for e in body["trace"]] == _EXPECTED_STAGES


async def test_trace_endpoint_404_for_unknown_email(client):
    resp = await client.get("/api/v1/emails/999999/trace")
    assert resp.status_code == 404


async def test_two_emails_traces_are_isolated(client):
    """Each email's trace holds only its own stage records."""
    id1 = (await client.post("/api/v1/emails/ingest", json=_SAMPLE_EMAIL)).json()[
        "email_id"
    ]
    id2 = (await client.post("/api/v1/emails/ingest", json=_SAMPLE_EMAIL)).json()[
        "email_id"
    ]
    assert id1 != id2
    assert len(read_traces(id1)) == 4
    assert len(read_traces(id2)) == 4
    assert all(e["email_id"] == id1 for e in read_traces(id1))


def test_tracer_buffers_until_flush(tmp_path):
    """PipelineTracer writes nothing until flush(), then writes in order."""
    configure_tracing(tmp_path / "trace.jsonl")
    tracer = PipelineTracer()
    with tracer.stage("classifier", {"body_length": 10}) as st:
        st.output_summary = {"intent": "general_inquiry"}
    with tracer.stage("router", {"intent": "general_inquiry"}) as st:
        st.output_summary = {"lane": "faq"}

    # Nothing persisted before flush.
    assert read_traces("42") == []
    tracer.flush("42")
    stages = [e["stage"] for e in read_traces("42")]
    assert stages == ["classifier", "router"]
