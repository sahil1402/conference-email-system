import pytest_asyncio
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from app.db.database import Base
from app.api.v1 import emails as emails_api

@pytest_asyncio.fixture
async def adb():
    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()

async def _mk(adb, draft):
    return await emails_api.email_repo.create_email(adb, {
        "sender": "a@b.edu", "subject": "s", "body": "b", "status": "draft_generated",
        "draft": draft})

async def test_gap_resolving_edit_schedules_learning(adb):
    e = await _mk(adb, {"draft_text": "Original with [CHAIR: the deadline?]"})
    bt = BackgroundTasks()
    await emails_api.approve_email(str(e.id), emails_api.ApproveRequest(
        approved_by="Chair1", final_text="Original with the deadline is Aug 5."), bt, adb)
    assert len(bt.tasks) == 1  # learning scheduled

async def test_no_placeholder_edit_does_not_schedule(adb):
    e = await _mk(adb, {"draft_text": "A complete answer."})
    bt = BackgroundTasks()
    await emails_api.approve_email(str(e.id), emails_api.ApproveRequest(
        approved_by="Chair1", final_text="A slightly reworded complete answer."), bt, adb)
    assert len(bt.tasks) == 0  # no [CHAIR:] gap → no learning
