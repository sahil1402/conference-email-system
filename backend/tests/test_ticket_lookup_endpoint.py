"""GET /emails/by-ticket/{ticket_id} — route, happy path (Piece B2).

Exercises the endpoint over the real ASGI app (httpx + ASGITransport, in-memory
SQLite via the get_db override), same harness as test_queue_facets.py. 404
handling for an unknown ticket is deliberately NOT covered here — that lands in
B3; this step only proves the found case + FastAPI's int-path 422.
"""

from types import SimpleNamespace

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
from app.db.database import Base, get_db
from app.db.models import Email
from app.repositories.audit_repository import AuditRepository

_TICKET_ID = 21567


async def _seed(session):
    # The Zendesk row we look up, plus an audit entry so the trail is non-empty
    # (proves audit_trail is actually populated, not just an empty list).
    zendesk = Email(
        sender="requester@univ.edu",
        subject="How do I update my paper?",
        body="body",
        status="DRAFT_GENERATED",
        classification={"intent": "submission_deadline", "confidence": 0.9},
        routing={"lane": "human_review"},
        source="zendesk",
        zendesk_ticket_id=_TICKET_ID,
        zendesk_status="open",
    )
    # A non-Zendesk row so the WHERE has to select, not just return the only row.
    toy = Email(
        sender="other@univ.edu", subject="unrelated", body="b",
        status="DRAFT_GENERATED", source="toy_dataset",
    )
    session.add_all([zendesk, toy])
    await session.commit()
    await session.refresh(zendesk)
    await AuditRepository().log_action(
        session, str(zendesk.id), "classified", "pipeline", {"note": "seed"}
    )
    await session.commit()


@pytest_asyncio.fixture
async def ctx():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        await _seed(session)

    async def _override_get_db():
        async with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield SimpleNamespace(client=client)
    await client.aclose()
    main.app.dependency_overrides.clear()
    await engine.dispose()


async def test_by_ticket_found_returns_email_and_trail(ctx):
    resp = await ctx.client.get(f"/api/v1/emails/by-ticket/{_TICKET_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"email", "audit_trail"}
    assert body["email"]["zendesk_ticket_id"] == _TICKET_ID
    assert body["email"]["source"] == "zendesk"
    # The seeded audit entry is present in the trail.
    assert [a["action"] for a in body["audit_trail"]] == ["classified"]


async def test_by_ticket_shape_matches_get_by_id(ctx):
    """The by-ticket response is byte-identical to GET /emails/{id} for the same
    row — the guarantee that the two endpoints share a response shape exactly."""
    by_ticket = await ctx.client.get(f"/api/v1/emails/by-ticket/{_TICKET_ID}")
    email_id = by_ticket.json()["email"]["id"]
    by_id = await ctx.client.get(f"/api/v1/emails/{email_id}")
    assert by_id.status_code == 200
    assert by_ticket.json() == by_id.json()


async def test_by_ticket_non_numeric_is_422(ctx):
    # ticket_id is typed int on the path → FastAPI rejects a non-numeric value
    # with 422 before the handler runs (no custom validation needed).
    resp = await ctx.client.get("/api/v1/emails/by-ticket/not-a-number")
    assert resp.status_code == 422


# --- B3: 404 handling -----------------------------------------------------
async def test_by_ticket_unknown_id_is_clean_404(ctx):
    # A valid integer with no matching row → a clean 404 JSON body, NOT a 500 /
    # unhandled exception from calling .id on None.
    resp = await ctx.client.get("/api/v1/emails/by-ticket/99999999")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "No email found for ticket id 99999999"}


async def test_by_ticket_null_ticket_row_never_matched(ctx):
    """Endpoint-level counterpart to B1's repo test: the seeded non-Zendesk row
    (zendesk_ticket_id IS NULL) is reachable by its DB id but must never be
    returned by a by-ticket lookup — the full request path 404s."""
    # The toy row exists and is fetchable by its internal id...
    all_rows = (await ctx.client.get("/api/v1/emails/queue")).json()["emails"]
    toy = next(e for e in all_rows if e["source"] == "toy_dataset")
    assert toy["zendesk_ticket_id"] is None
    by_id = await ctx.client.get(f"/api/v1/emails/{toy['id']}")
    assert by_id.status_code == 200
    # ...but a by-ticket lookup using that row's DB id as a "ticket id" 404s,
    # confirming a NULL ticket_id row is never matched.
    resp = await ctx.client.get(f"/api/v1/emails/by-ticket/{toy['id']}")
    assert resp.status_code == 404
