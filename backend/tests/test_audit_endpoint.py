"""Endpoint tests for the audit log API (GET /api/v1/audit + /{log_id}).

Unlike the pipeline unit tests (which mock the session), these exercise the
real router against a throwaway in-memory SQLite DB. A StaticPool keeps every
connection pointed at the same ``:memory:`` database for the test's lifetime,
the ``get_db`` dependency is overridden to use that DB, and the app is driven
through httpx's ASGITransport. No real DB file and no network are touched.
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
from app.db.database import Base, get_db
from app.db.models import Email
from app.repositories.audit_repository import AuditRepository


class _Ctx:
    """Bundle the test client with the ids it needs to assert against."""

    def __init__(self, client: httpx.AsyncClient, email1_id: int, email2_id: int):
        self.client = client
        self.email1_id = email1_id
        self.email2_id = email2_id


@pytest_asyncio.fixture
async def ctx():
    """In-memory DB + seeded audit logs + an httpx client wired to the app.

    Seeds two emails: email1 has 3 audit logs, email2 has 1 — so the table
    holds 4 entries total, and filtering by email1 should yield exactly 3.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    repo = AuditRepository()
    async with factory() as session:
        email1 = Email(sender="author@university.edu", subject="Q1", body="b1")
        email2 = Email(sender="other@university.edu", subject="Q2", body="b2")
        session.add_all([email1, email2])
        await session.commit()
        await session.refresh(email1)
        await session.refresh(email2)

        for action in ("classified", "routed", "approved"):
            await repo.create_audit_log(
                session,
                email_id=str(email1.id),
                action=action,
                actor="chair",
                details={"note": action},
            )
        await repo.create_audit_log(
            session,
            email_id=str(email2.id),
            action="drafted",
            actor="system",
            details=None,
        )
        email1_id, email2_id = email1.id, email2.id

    async def _override_get_db():
        async with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield _Ctx(client, email1_id, email2_id)

    main.app.dependency_overrides.clear()
    await engine.dispose()


async def test_list_returns_200_with_pagination_keys(ctx):
    resp = await ctx.client.get("/api/v1/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["total"] == 4
    assert len(body["items"]) == 4
    # Newest-first ordering: the last-inserted log ("drafted") comes first.
    assert body["items"][0]["action"] == "drafted"
    # Response surfaces the schema-mapped fields, not the raw column names.
    first = body["items"][0]
    assert set(first) >= {"id", "email_id", "action", "actor", "details", "created_at"}


async def test_list_respects_limit(ctx):
    resp = await ctx.client.get("/api/v1/audit", params={"limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 5
    assert len(body["items"]) <= 5
    # total ignores pagination — it is the full matching count.
    assert body["total"] == 4

    # A tighter limit genuinely caps the page while total stays whole.
    resp2 = await ctx.client.get("/api/v1/audit", params={"limit": 2})
    body2 = resp2.json()
    assert len(body2["items"]) == 2
    assert body2["total"] == 4


async def test_get_nonexistent_returns_404(ctx):
    resp = await ctx.client.get("/api/v1/audit/999999")
    assert resp.status_code == 404


async def test_filter_by_email_id(ctx):
    resp = await ctx.client.get(
        "/api/v1/audit", params={"email_id": str(ctx.email1_id)}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert all(item["email_id"] == ctx.email1_id for item in body["items"])


async def test_get_single_log_maps_aliased_fields(ctx):
    # Grab a real id from the list, then fetch it directly.
    listing = (await ctx.client.get("/api/v1/audit")).json()
    target = listing["items"][0]
    resp = await ctx.client.get(f"/api/v1/audit/{target['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == target["id"]
    assert body["action"] == target["action"]
    # created_at (<- timestamp) and details (<- extra_metadata) are present.
    assert body["created_at"] is not None
    assert "details" in body
