"""Async SQLAlchemy persistence setup.

Provides the declarative `Base`, a lazily-created async engine + session
factory, and the `get_db` FastAPI dependency. The engine URL is derived from
`settings.DATABASE_URL`, normalized to an async driver (aiosqlite for SQLite).
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


def _to_async_url(url: str) -> str:
    """Normalize a database URL to use an async driver.

    SQLite's default sync driver is swapped for aiosqlite. Other backends are
    returned unchanged (configure them with an async driver in DATABASE_URL).
    """
    if url.startswith("sqlite+aiosqlite"):
        return url
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


ASYNC_DATABASE_URL = _to_async_url(settings.DATABASE_URL)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# The engine is built from settings.DATABASE_URL — never a hardcoded string.
# For production, set DATABASE_URL in .env to postgresql+asyncpg://user:password@host:5432/confmail
# (the URL passes through _to_async_url unchanged; only SQLite is rewritten to aiosqlite).
engine = create_async_engine(ASYNC_DATABASE_URL, echo=False, future=True)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a scoped async session."""
    async with async_session_factory() as session:
        yield session
