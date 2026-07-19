"""EmailRepository helpers for the re-evaluation sweep."""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Email
from app.models.enums import EmailStatus
from app.repositories.email_repository import EmailRepository


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


async def _make_email(db, status: str) -> Email:
    e = Email(sender="a@b.com", subject="s", body="b", status=status)
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return e


async def test_get_open_tickets_returns_only_draft_generated(session):
    repo = EmailRepository()
    open_e = await _make_email(session, EmailStatus.DRAFT_GENERATED.value)
    await _make_email(session, EmailStatus.APPROVED.value)
    await _make_email(session, EmailStatus.PENDING.value)

    tickets = await repo.get_open_tickets(session)
    assert [t.id for t in tickets] == [open_e.id]


async def test_set_redrafting_toggles_flag(session):
    repo = EmailRepository()
    e = await _make_email(session, EmailStatus.DRAFT_GENERATED.value)

    updated = await repo.set_redrafting(session, str(e.id), True)
    assert updated is not None and updated.redrafting is True

    cleared = await repo.set_redrafting(session, str(e.id), False)
    assert cleared.redrafting is False


async def test_save_redraft_overwrites_and_clears_flag(session):
    repo = EmailRepository()
    e = await _make_email(session, EmailStatus.DRAFT_GENERATED.value)
    await repo.set_redrafting(session, str(e.id), True)

    saved = await repo.save_redraft(
        session,
        str(e.id),
        draft={"draft_text": "new", "placeholders": []},
        routing={"lane": "faq"},
        retrieval_context={"query": "q", "intent": "", "retrieved_ids": ["policy_1"]},
    )
    assert saved is not None
    assert saved.draft["draft_text"] == "new"
    assert saved.routing["lane"] == "faq"
    assert saved.retrieval_context["retrieved_ids"] == ["policy_1"]
    assert saved.redrafting is False  # cleared as part of the save


async def test_reeval_helpers_return_none_for_missing(session):
    repo = EmailRepository()
    assert await repo.set_redrafting(session, "999999", True) is None
    assert await repo.save_redraft(
        session, "999999", draft={}, routing={}, retrieval_context={}
    ) is None


async def test_claim_for_redraft_is_atomic_and_single_winner(session):
    repo = EmailRepository()
    e = await _make_email(session, EmailStatus.DRAFT_GENERATED.value)

    first = await repo.claim_for_redraft(session, str(e.id))
    assert first is True                      # flipped False→True

    second = await repo.claim_for_redraft(session, str(e.id))
    assert second is False                     # already claimed → refused

    await session.refresh(e)
    assert e.redrafting is True


async def test_claim_refused_when_not_open(session):
    repo = EmailRepository()
    e = await _make_email(session, EmailStatus.APPROVED.value)  # not an open draft
    assert await repo.claim_for_redraft(session, str(e.id)) is False
    await session.refresh(e)
    assert e.redrafting is False


async def test_save_redraft_refuses_when_unclaimed(session):
    repo = EmailRepository()
    e = await _make_email(session, EmailStatus.DRAFT_GENERATED.value)
    e.draft = {"draft_text": "old"}
    await session.commit()
    # redrafting is False (never claimed) → save must refuse and not clobber.
    saved = await repo.save_redraft(
        session, str(e.id),
        draft={"draft_text": "new"}, routing={"lane": "faq"},
        retrieval_context={"query": "q", "intent": "", "retrieved_ids": []},
    )
    assert saved is None
    await session.refresh(e)
    assert e.draft["draft_text"] == "old"      # untouched


async def test_save_redraft_refuses_when_approved_after_claim(session):
    repo = EmailRepository()
    e = await _make_email(session, EmailStatus.DRAFT_GENERATED.value)
    e.draft = {"draft_text": "old"}
    await session.commit()
    assert await repo.claim_for_redraft(session, str(e.id)) is True
    # Chair approves in the claim→save window.
    e.status = EmailStatus.APPROVED.value
    await session.commit()
    saved = await repo.save_redraft(
        session, str(e.id),
        draft={"draft_text": "new"}, routing={"lane": "faq"},
        retrieval_context={"query": "q", "intent": "", "retrieved_ids": []},
    )
    assert saved is None                       # status no longer draft_generated
    await session.refresh(e)
    assert e.draft["draft_text"] == "old"      # chair's approved content preserved


async def test_clear_all_redrafting_flags(session):
    repo = EmailRepository()
    stuck = await _make_email(session, EmailStatus.DRAFT_GENERATED.value)
    await repo.set_redrafting(session, str(stuck.id), True)
    clean = await _make_email(session, EmailStatus.DRAFT_GENERATED.value)

    cleared = await repo.clear_all_redrafting_flags(session)
    assert cleared == 1                        # only the one set flag
    await session.refresh(stuck)
    await session.refresh(clean)
    assert stuck.redrafting is False
    assert clean.redrafting is False
