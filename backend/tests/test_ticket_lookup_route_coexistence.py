"""B4 regression: the new /emails/by-ticket/{ticket_id} route coexists with the
existing routes without shadowing or being shadowed.

The risk the route ordering guards against: /{email_id} is a single-segment
catch-all, so a static/prefixed path declared AFTER it can be captured. This
suite proves all four routes resolve independently and correctly, and that
by-ticket does a TICKET-id lookup while /{email_id} does a DB-id lookup (they
are not the same route in disguise). No functional code is exercised beyond the
public HTTP surface.
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

# Deliberately LARGE so it can't collide with any autoincrement DB primary key
# in this fixture — lets us prove by-ticket(ticket) and /{db_id} are distinct.
_TICKET_ID = 21567


async def _seed(session):
    zendesk = Email(
        sender="requester@univ.edu", subject="zendesk row", body="b",
        status="DRAFT_GENERATED", routing={"lane": "human_review"},
        source="zendesk", zendesk_ticket_id=_TICKET_ID, zendesk_status="open",
    )
    toy = Email(
        sender="other@univ.edu", subject="toy row", body="b",
        status="DRAFT_GENERATED", routing={"lane": "faq"}, source="toy_dataset",
    )
    session.add_all([zendesk, toy])
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


async def test_by_ticket_not_shadowed_and_independent_from_by_id(ctx):
    """by-ticket resolves to the ticket-id handler (not captured by /{email_id}),
    and /{email_id} resolves by DB id — the two are genuinely different routes."""
    # by-ticket → the Zendesk row, reached via its ticket id.
    bt = await ctx.client.get(f"/api/v1/emails/by-ticket/{_TICKET_ID}")
    assert bt.status_code == 200
    row = bt.json()["email"]
    assert row["source"] == "zendesk"
    assert row["zendesk_ticket_id"] == _TICKET_ID

    db_id = row["id"]
    # Same row, reached via /{email_id} using its DB primary key.
    by_id = await ctx.client.get(f"/api/v1/emails/{db_id}")
    assert by_id.status_code == 200
    assert by_id.json()["email"]["id"] == db_id

    # The clincher that the routes are independent: /{email_id} with the TICKET
    # id (which is not a DB pk here) is a DB-id lookup → 404, NOT the zendesk row.
    # If by-ticket were shadowing (or aliasing) /{email_id}, this would 200.
    stray = await ctx.client.get(f"/api/v1/emails/{_TICKET_ID}")
    assert stray.status_code == 404


async def test_get_by_id_unchanged(ctx):
    """Existing DB-id lookup still returns the {email, audit_trail} envelope."""
    # Grab a real DB id from the queue, then fetch it by id.
    rows = (await ctx.client.get("/api/v1/emails/queue")).json()["emails"]
    db_id = rows[0]["id"]
    resp = await ctx.client.get(f"/api/v1/emails/{db_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"email", "audit_trail"}
    assert body["email"]["id"] == db_id
    # Unknown DB id still 404s (behavior unchanged).
    assert (await ctx.client.get("/api/v1/emails/98765432")).status_code == 404


async def test_queue_unchanged(ctx):
    resp = await ctx.client.get("/api/v1/emails/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2  # the two seeded rows
    assert {e["source"] for e in body["emails"]} == {"zendesk", "toy_dataset"}


async def test_queue_facets_unchanged(ctx):
    resp = await ctx.client.get("/api/v1/emails/queue/facets")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"by_zendesk_status", "by_source", "sources"}
    assert body["by_source"] == {"zendesk": 1, "toy_dataset": 1}
    assert body["by_zendesk_status"] == {"open": 1}
    assert sorted(body["sources"]) == ["toy_dataset", "zendesk"]
