"""EmailRepository.get_email_by_zendesk_ticket_id (Piece B1)."""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Email
from app.models.enums import EmailSource
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


async def _make_email(db, *, ticket_id: int | None) -> Email:
    e = Email(
        sender="a@b.com",
        subject="s",
        body="b",
        source=(
            EmailSource.ZENDESK.value
            if ticket_id is not None
            else EmailSource.TOY_DATASET.value
        ),
        zendesk_ticket_id=ticket_id,
    )
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return e


async def test_returns_email_with_matching_ticket_id(session):
    repo = EmailRepository()
    target = await _make_email(session, ticket_id=21567)
    # A second Zendesk row so we prove the WHERE actually selects, not just
    # returns the only row present.
    await _make_email(session, ticket_id=99999)

    found = await repo.get_email_by_zendesk_ticket_id(session, 21567)
    assert found is not None
    assert found.id == target.id
    assert found.zendesk_ticket_id == 21567


async def test_returns_none_when_no_match(session):
    repo = EmailRepository()
    await _make_email(session, ticket_id=21567)

    assert await repo.get_email_by_zendesk_ticket_id(session, 12345) is None


async def test_null_ticket_id_row_never_matched(session):
    repo = EmailRepository()
    # A non-Zendesk row (zendesk_ticket_id IS NULL) must never be returned by a
    # ticket-id lookup, whatever integer is queried.
    null_row = await _make_email(session, ticket_id=None)
    assert null_row.zendesk_ticket_id is None

    for ticket_id in (0, 1, null_row.id, 21567):
        assert await repo.get_email_by_zendesk_ticket_id(session, ticket_id) is None
