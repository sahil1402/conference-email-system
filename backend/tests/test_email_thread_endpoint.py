"""Tests for GET /api/v1/emails/{id}/thread (Piece T3).

Drives the real FastAPI app via httpx ASGITransport against an in-memory async
SQLite DB (StaticPool keeps one :memory: DB across connections). Seeds Email +
EmailThreadMessage + EmailProcessingResult rows directly through the ORM, then
asserts the endpoint's ordering, per-message result history, latest-id pointer,
empty cases, and 404.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import main
from app.db.database import Base, get_db
from app.db.models import Email, EmailProcessingResult, EmailThreadMessage
from app.models.enums import EmailSource, MessageAuthorRole

_T0 = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)


def _at(minutes: int) -> datetime:
    return _T0 + timedelta(minutes=minutes)


async def _make_ctx(seed):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ids = {}
    async with factory() as session:
        ids = await seed(session)
        await session.commit()

    async def _override_get_db():
        async with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    return SimpleNamespace(client=client, engine=engine, ids=ids)


def _msg(email_id, cid, created, *, public=True, role=MessageAuthorRole.END_USER.value):
    return EmailThreadMessage(
        email_id=email_id,
        zendesk_comment_id=cid,
        public=public,
        author_role=role,
        author_id=500,
        plain_body=f"body-{cid}",
        html_body=f"<p>body-{cid}</p>",
        created_at=created,
        via_channel="email",
    )


def _result(msg_id, created, *, lane="HUMAN_REVIEW", confidence=0.7):
    return EmailProcessingResult(
        thread_message_id=msg_id,
        classification={"intent": "author_list_change", "confidence": confidence},
        routing={"lane": lane},
        draft={"draft_text": f"draft@{created.isoformat()}"},
        retrieval_context={"query": "q", "retrieved_ids": ["policy_101"]},
        lane=lane,
        confidence=confidence,
        created_at=created,
    )


# --- seeds ------------------------------------------------------------------


async def _seed_zendesk_thread(session) -> dict:
    """A Zendesk ticket: initial inquiry + a chair reply + a requester follow-up.

    The follow-up (msg C) has TWO processing results (a reprocess) so history +
    latest-id can be checked. The chair reply (msg B) has NO results.
    """
    email = Email(
        sender="author@univ.edu",
        subject="Ticket subject",
        body="initial inquiry body",
        status="DRAFT_GENERATED",
        source=EmailSource.ZENDESK.value,
        zendesk_ticket_id=4242,
        classification={"intent": "author_list_change", "confidence": 0.9},
        routing={"lane": "human_review"},
    )
    session.add(email)
    await session.flush()

    # Insert OUT of chronological order to prove the endpoint sorts.
    msg_c = _msg(email.id, 9003, _at(20))  # requester follow-up (latest)
    msg_a = _msg(email.id, 9001, _at(0))   # initial inquiry (earliest)
    msg_b = _msg(email.id, 9002, _at(10), role=MessageAuthorRole.AGENT.value)  # chair
    session.add_all([msg_c, msg_a, msg_b])
    await session.flush()

    # Follow-up msg_c: two results (reprocess history), inserted newest-first.
    r_late = _result(msg_c.id, _at(25), lane="AUTO_REPLY", confidence=0.88)
    r_early = _result(msg_c.id, _at(21), lane="HUMAN_REVIEW", confidence=0.61)
    session.add_all([r_late, r_early])
    await session.flush()

    return {
        "email_id": email.id,
        "msg_a": msg_a.id,
        "msg_b": msg_b.id,
        "msg_c": msg_c.id,
        "r_early": r_early.id,
        "r_late": r_late.id,
    }


async def _seed_toy_no_thread(session) -> dict:
    email = Email(
        sender="x@univ.edu",
        subject="toy",
        body="body",
        status="DRAFT_GENERATED",
        source=EmailSource.TOY_DATASET.value,
        classification={"intent": "submission_deadline", "confidence": 0.95},
        routing={"lane": "human_review"},
    )
    session.add(email)
    await session.flush()
    return {"email_id": email.id}


# --- fixtures ---------------------------------------------------------------


@pytest_asyncio.fixture
async def zendesk_ctx():
    c = await _make_ctx(_seed_zendesk_thread)
    yield c
    await c.client.aclose()
    main.app.dependency_overrides.clear()
    await c.engine.dispose()


@pytest_asyncio.fixture
async def toy_ctx():
    c = await _make_ctx(_seed_toy_no_thread)
    yield c
    await c.client.aclose()
    main.app.dependency_overrides.clear()
    await c.engine.dispose()


# --- tests ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_messages_chronological(zendesk_ctx):
    """Messages come back oldest-first regardless of insertion order."""
    eid = zendesk_ctx.ids["email_id"]
    resp = await zendesk_ctx.client.get(f"/api/v1/emails/{eid}/thread")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email_id"] == eid
    assert [m["zendesk_comment_id"] for m in body["messages"]] == [9001, 9002, 9003]


@pytest.mark.asyncio
async def test_processing_results_oldest_first_and_latest_id(zendesk_ctx):
    """The follow-up's results are oldest-first; latest id points to the newest."""
    eid = zendesk_ctx.ids["email_id"]
    body = (await zendesk_ctx.client.get(f"/api/v1/emails/{eid}/thread")).json()
    followup = next(m for m in body["messages"] if m["zendesk_comment_id"] == 9003)

    result_ids = [r["id"] for r in followup["processing_results"]]
    assert result_ids == [zendesk_ctx.ids["r_early"], zendesk_ctx.ids["r_late"]]
    # Oldest-first: confidences in insertion-by-time order.
    assert [r["confidence"] for r in followup["processing_results"]] == [0.61, 0.88]
    # latest points at the newest (last) result.
    assert followup["latest_processing_result_id"] == zendesk_ctx.ids["r_late"]
    # Full pipeline output is present on each result.
    latest = followup["processing_results"][-1]
    assert latest["lane"] == "AUTO_REPLY"
    assert latest["routing"] == {"lane": "AUTO_REPLY"}
    assert latest["classification"]["intent"] == "author_list_change"
    assert latest["retrieval_context"]["retrieved_ids"] == ["policy_101"]


@pytest.mark.asyncio
async def test_message_with_no_results_is_empty_not_error(zendesk_ctx):
    """A chair reply (or a failed follow-up) has zero results: [] and null id."""
    eid = zendesk_ctx.ids["email_id"]
    body = (await zendesk_ctx.client.get(f"/api/v1/emails/{eid}/thread")).json()
    for cid in (9001, 9002):  # initial inquiry + chair reply — no per-msg results
        msg = next(m for m in body["messages"] if m["zendesk_comment_id"] == cid)
        assert msg["processing_results"] == []
        assert msg["latest_processing_result_id"] is None


@pytest.mark.asyncio
async def test_toy_email_empty_thread(toy_ctx):
    """A non-Zendesk email with no thread returns {email_id, messages: []}."""
    eid = toy_ctx.ids["email_id"]
    resp = await toy_ctx.client.get(f"/api/v1/emails/{eid}/thread")
    assert resp.status_code == 200
    assert resp.json() == {"email_id": eid, "messages": []}


@pytest.mark.asyncio
async def test_unknown_email_returns_404(toy_ctx):
    resp = await toy_ctx.client.get("/api/v1/emails/999999/thread")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_non_numeric_email_id_returns_404(toy_ctx):
    """A non-numeric id resolves to not-found (repo coerces → None), not a 500."""
    resp = await toy_ctx.client.get("/api/v1/emails/not-an-id/thread")
    assert resp.status_code == 404
