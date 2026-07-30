import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from app.db.database import Base
from app.repositories.suggestion_repository import SuggestionRepository

repo = SuggestionRepository()

@pytest_asyncio.fixture
async def adb():
    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()

async def _mk(adb, title="Add authors after freeze", content="Authors may not be added after the freeze."):
    return await repo.create(adb, source_email_id=1, experience_summary="e", title=title, content=content,
                             category=None, intents=["submission_and_format"], generalizable=True,
                             reason="r", confidence=0.8, conflict_report=None)

async def test_create_list_get_count(adb):
    s = await _mk(adb)
    assert (await repo.get(adb, s.id)).title == "Add authors after freeze"
    assert len(await repo.list(adb, status="pending")) == 1
    assert await repo.count_pending(adb) == 1

async def test_reject_keeps_row_and_drops_pending_count(adb):
    s = await _mk(adb)
    await repo.reject(adb, s.id, actor="Chair1", reason="dup")
    assert (await repo.get(adb, s.id)).status == "rejected"
    assert await repo.count_pending(adb) == 0

async def test_mark_accepted_links_key(adb):
    s = await _mk(adb)
    await repo.mark_accepted(adb, s.id, actor="Chair1", policy_key="int_x")
    got = await repo.get(adb, s.id)
    assert got.status == "accepted" and got.resulting_policy_key == "int_x"

async def test_bump_seen(adb):
    s = await _mk(adb)
    await repo.bump_seen(adb, s.id)
    assert (await repo.get(adb, s.id)).seen_count == 2

async def test_find_similar_matches_pending_and_rejected(adb):
    await _mk(adb)  # pending
    hit = await repo.find_similar(adb, title="Adding authors after the freeze",
                                  content="Authors cannot be added after the freeze.")
    assert hit is not None
    miss = await repo.find_similar(adb, title="Poster print size", content="Posters are A0 portrait.")
    assert miss is None
