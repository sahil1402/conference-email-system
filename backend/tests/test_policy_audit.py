import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, PolicyAuditLog
from app.repositories.policy_audit_repository import PolicyAuditRepository


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with f() as s:
        yield s
    await engine.dispose()


async def test_policy_audit_log_persists(session):
    repo = PolicyAuditRepository()
    entry = await repo.log(session, policy_key="int_x", action="policy_created",
                           actor="chair:1", before=None, after={"title": "T"})
    assert entry.id is not None
    rows = (await session.execute(select(PolicyAuditLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].action == "policy_created"
    assert rows[0].after == {"title": "T"}


async def test_policy_audit_list_newest_first(session):
    repo = PolicyAuditRepository()
    await repo.log(session, policy_key="a", action="policy_created", actor="chair:1")
    await repo.log(session, policy_key="a", action="policy_retired", actor="chair:1")
    entries = await repo.list(session)
    assert [e.action for e in entries] == ["policy_retired", "policy_created"]   # newest first
