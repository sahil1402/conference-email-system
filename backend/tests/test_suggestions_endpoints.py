import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from app.db.database import Base
from app.api.v1 import policies as papi
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

async def _mk(adb):
    return await repo.create(adb, source_email_id=1, experience_summary="e", title="T", content="C",
                             category=None, intents=[], generalizable=True, reason="r", confidence=0.5, conflict_report=None)

async def test_list_and_count(adb):
    await _mk(adb)
    out = await papi.list_suggestions(status="pending", limit=200, offset=0, db=adb)
    assert len(out["suggestions"]) == 1
    assert (await papi.suggestions_count(db=adb))["pending"] == 1

async def test_reject_then_count_zero(adb):
    s = await _mk(adb)
    r = await papi.reject_suggestion(s.id, papi.RejectSuggestionRequest(actor="Chair1", reason="dup"), adb)
    assert r["status"] == "rejected"
    assert (await papi.suggestions_count(db=adb))["pending"] == 0

async def test_accept_links_key(adb):
    s = await _mk(adb)
    r = await papi.accept_suggestion(s.id, papi.AcceptSuggestionRequest(actor="Chair1", policy_key="int_x"), adb)
    assert r["status"] == "accepted" and r["resulting_policy_key"] == "int_x"

async def test_reject_missing_404(adb):
    try:
        await papi.reject_suggestion(9999, papi.RejectSuggestionRequest(actor="Chair1"), adb)
        assert False
    except HTTPException as e:
        assert e.status_code == 404
