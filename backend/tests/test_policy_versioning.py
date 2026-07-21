"""Versioning/lineage tests: model defaults + PATCH /edit + POST /revert-edit.

Same throwaway-in-memory-SQLite harness as test_policies_endpoint.py.
"""
import httpx
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


async def test_new_policy_lineage_defaults(client):
    _, factory = client
    async with factory() as s:
        s.add(PolicyDocument(policy_key="policy_1", title="t", content="c"))
        await s.commit()
        row = (await s.execute(select(PolicyDocument).where(PolicyDocument.policy_key == "policy_1"))).scalar_one()
        assert row.version == 1
        assert row.supersedes is None
        assert row.superseded_by is None
        assert row.root_key is None
