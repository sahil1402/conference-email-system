import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from app.db.database import get_db
from app.db.models import Base, PolicyAuditLog, PolicyDocument


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
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


async def test_create_internal_policy_writes_row_and_audit(client):
    c, factory = client
    resp = await c.post("/api/v1/policies", json={
        "title": "Deadline extended", "content": "now March 5", "actor": "1"})
    assert resp.status_code == 201
    assert resp.json()["visibility"] == "internal"

    async with factory() as s:
        rows = (await s.execute(select(PolicyDocument))).scalars().all()
        assert len(rows) == 1 and rows[0].status == "active"
        audit = (await s.execute(select(PolicyAuditLog))).scalars().all()
        assert len(audit) == 1 and audit[0].action == "policy_created"


async def test_retire_missing_returns_404(client):
    c, _ = client
    resp = await c.patch("/api/v1/policies/nope/retire", json={"actor": "1"})
    assert resp.status_code == 404
