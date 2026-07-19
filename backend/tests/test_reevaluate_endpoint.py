"""POST /policies/reevaluate schedules a sweep and reports the open count."""

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from app.db.database import get_db
from app.db.models import Base, Email
from app.models.enums import EmailStatus


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as s:
            yield s

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, factory
    main.app.dependency_overrides.clear()
    await engine.dispose()


async def _seed_open(factory, n: int) -> None:
    async with factory() as s:
        for _ in range(n):
            s.add(Email(
                sender="a@b.com", subject="s", body="b",
                status=EmailStatus.DRAFT_GENERATED.value,
                retrieval_context={"query": "q", "intent": "", "retrieved_ids": []},
            ))
        await s.commit()


async def test_reevaluate_returns_open_count_and_schedules(client, monkeypatch):
    c, factory = client
    await _seed_open(factory, 3)

    ran = {}

    async def _fake_sweep(*a, **k):
        ran["called"] = True

    # The background task would otherwise hit the real DB — isolate the endpoint.
    monkeypatch.setattr("app.api.v1.policies.reevaluate_open_tickets", _fake_sweep)

    resp = await c.post("/api/v1/policies/reevaluate")
    assert resp.status_code == 202
    body = resp.json()
    assert body["open"] == 3
    assert body["scheduled"] is True
    # httpx awaits the full response incl. background tasks → the sweep ran.
    assert ran.get("called") is True


async def test_reevaluate_zero_open(client, monkeypatch):
    c, _factory = client
    monkeypatch.setattr(
        "app.api.v1.policies.reevaluate_open_tickets", lambda *a, **k: None
    )
    resp = await c.post("/api/v1/policies/reevaluate")
    assert resp.status_code == 202
    assert resp.json() == {"open": 0, "scheduled": True}
