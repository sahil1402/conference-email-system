import pytest_asyncio
from types import SimpleNamespace
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from app.db.database import Base
from app.db.models import Email
from app.models.enums import EmailSource
from app.repositories.suggestion_repository import SuggestionRepository
from app.pipeline import experience_learning as el

repo = SuggestionRepository()

@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

async def _seed_email(factory, draft):
    async with factory() as db:
        e = Email(sender="a@b.edu", subject="Add author", body="Can you add my coauthor?",
                  status="approved", source=EmailSource.ZENDESK.value,
                  classification={"intent": "submission_and_format", "confidence": 0.6},
                  draft=draft)
        db.add(e); await db.commit(); await db.refresh(e); return e.id

async def test_generalizable_persists_pending(factory, monkeypatch):
    async def fake(**kw):
        return {"generalizable": True, "reason": "reusable rule", "confidence": 0.8,
                "title": "Author additions after freeze", "content": "Authors cannot be added after the freeze.",
                "category": None, "intents": ["submission_and_format"], "experience_summary": "chair denied late author add"}
    monkeypatch.setattr(el, "_judge_and_condense", fake)
    monkeypatch.setattr(el, "detect_conflicts", _asyncnone)
    eid = await _seed_email(factory, {"original_draft_text": "... [CHAIR: can an author be added now?] ...",
                                      "draft_text": "Authors cannot be added after the freeze.", "is_edited": True})
    await el.learn_from_edit(str(eid), session_factory=factory)
    async with factory() as db:
        assert await repo.count_pending(db) == 1

async def test_one_off_persists_nothing(factory, monkeypatch):
    async def fake(**kw):
        return {"generalizable": False, "reason": "ticket-specific", "confidence": 0.9,
                "title": "", "content": "", "category": None, "intents": [], "experience_summary": "x"}
    monkeypatch.setattr(el, "_judge_and_condense", fake)
    eid = await _seed_email(factory, {"original_draft_text": "[CHAIR: x]", "draft_text": "y", "is_edited": True})
    await el.learn_from_edit(str(eid), session_factory=factory)
    async with factory() as db:
        assert await repo.count_pending(db) == 0

async def test_gated_no_model_returns_none(monkeypatch):
    monkeypatch.setattr(el.settings, "MODEL_PROVIDER", "fallback")
    out = await el._judge_and_condense(original_draft="[CHAIR: x]", final_text="y", inquiry="q", intent="i", policy_context="")
    assert out is None

async def test_dedup_skips_pending_neardup(factory, monkeypatch):
    async def fake(**kw):
        return {"generalizable": True, "reason": "r", "confidence": 0.8,
                "title": "Authors after freeze", "content": "Authors cannot be added after the freeze.",
                "category": None, "intents": ["submission_and_format"], "experience_summary": "x"}
    monkeypatch.setattr(el, "_judge_and_condense", fake)
    monkeypatch.setattr(el, "detect_conflicts", _asyncnone)
    d = {"original_draft_text": "[CHAIR: x]", "draft_text": "z", "is_edited": True}
    e1 = await _seed_email(factory, d); e2 = await _seed_email(factory, d)
    await el.learn_from_edit(str(e1), session_factory=factory)
    await el.learn_from_edit(str(e2), session_factory=factory)
    async with factory() as db:
        assert await repo.count_pending(db) == 1  # second was deduped

async def _asyncnone(*a, **k):
    return None
