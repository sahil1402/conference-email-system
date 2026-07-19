"""The emails table carries the re-eval columns (Phase G)."""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Email


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


def test_email_model_has_redraft_columns():
    cols = Email.__table__.columns
    assert "redrafting" in cols
    assert "retrieval_context" in cols
    # redrafting is a non-null boolean defaulting to False.
    assert cols["redrafting"].nullable is False
    # retrieval_context is nullable JSON.
    assert cols["retrieval_context"].nullable is True


async def test_redrafting_defaults_false_on_insert(session):
    email = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
    session.add(email)
    await session.commit()
    await session.refresh(email)
    assert email.redrafting is False
    assert email.retrieval_context is None
