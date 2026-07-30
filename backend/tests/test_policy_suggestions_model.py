import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy import select
from app.db.database import Base
from app.db.models import PolicySuggestion


@pytest_asyncio.fixture
async def adb():
    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def test_policy_suggestion_defaults(adb):
    row = PolicySuggestion(
        source_email_id=42, experience_summary="chair supplied X",
        title="T", content="C", intents=["submission_and_format"],
    )
    adb.add(row)
    await adb.commit()
    got = (await adb.execute(select(PolicySuggestion))).scalar_one()
    assert got.status == "pending"
    assert got.seen_count == 1
    assert got.generalizable is True
    assert got.resulting_policy_key is None
    assert got.category is None
