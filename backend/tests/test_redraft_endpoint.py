"""Per-email retry: reprocess_email (update-in-place) + POST /emails/{id}/redraft."""

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from app.db.database import get_db
from app.db.models import Base, Email
from app.models.enums import EmailStatus
from app.pipeline.orchestrator import EmailPipeline


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


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


# --- reprocess_email: updates the SAME row, never creates a new one ---------
async def test_reprocess_email_updates_in_place(session):
    pipeline = EmailPipeline()
    result = await pipeline.process_email(
        {"from": "a@b.com", "to": "c@d.org", "subject": "Deadline?",
         "body": "When is the submission deadline?", "timestamp": ""},
        session,
    )
    original_id = result.email_id
    total_before = (await session.execute(select(func.count()).select_from(Email))).scalar_one()

    email = await pipeline.email_repo.get_email_by_id(session, original_id)
    reprocessed = await pipeline.reprocess_email(session, email)

    # Same row (not a new one), fresh draft, flag cleared.
    assert reprocessed.email_id == original_id
    total_after = (await session.execute(select(func.count()).select_from(Email))).scalar_one()
    assert total_after == total_before == 1
    row = await pipeline.email_repo.get_email_by_id(session, original_id)
    assert row.draft is not None
    assert row.redrafting is False
    assert row.status == EmailStatus.DRAFT_GENERATED.value


# --- endpoint: flags redrafting + schedules the background reprocess ---------
async def test_redraft_endpoint_flags_and_schedules(client, monkeypatch):
    c, factory = client
    async with factory() as s:
        s.add(Email(sender="a@b.com", subject="s", body="b",
                    status=EmailStatus.DRAFT_GENERATED.value))
        await s.commit()

    ran = {}

    async def _fake_bg(email_id):
        ran["id"] = email_id

    # The real background task would hit the production DB/pipeline — isolate it.
    monkeypatch.setattr("app.api.v1.emails._redraft_email_bg", _fake_bg)

    resp = await c.post("/api/v1/emails/1/redraft")
    assert resp.status_code == 202
    assert resp.json() == {"email_id": "1", "redrafting": True}
    assert ran.get("id") == "1"  # background reprocess scheduled + ran

    async with factory() as s:
        row = (await s.execute(select(Email).where(Email.id == 1))).scalar_one()
        assert row.redrafting is True  # flagged so the UI shows "re-drafting…"


async def test_redraft_unknown_email_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr("app.api.v1.emails._redraft_email_bg", lambda *a, **k: None)
    resp = await c.post("/api/v1/emails/999999/redraft")
    assert resp.status_code == 404
