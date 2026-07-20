"""Regression tests for the lane-scoped queue endpoint (GET /api/v1/emails/queue).

Guards against the Auto-Replies "3 vs 9" bug: the page derived both its stat and
its table from a generic, hard-capped (limit=20) queue fetch and then filtered
client-side, silently dropping any faq-lane email outside the newest page. The
fix is a lane query param plus a lane-scoped ``total`` — so a caller asking for
``lane=faq`` gets the true count AND the full set of faq rows, regardless of how
the emails are ordered or paged.

The seed deliberately puts the faq emails OUTSIDE the newest-20 window (older
timestamps, lower ids) so the old approach would miss them entirely.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import main
from app.db.database import Base, get_db
from app.db.models import Email

_BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)
_N_HUMAN_REVIEW = 22  # > one page (default limit is 20)
_N_FAQ = 5


def _email(idx: int, lane: str, received_at: datetime) -> Email:
    return Email(
        sender=f"user{idx}@univ.edu",
        subject=f"{lane} email {idx}",
        body="body",
        status="DRAFT_GENERATED",
        classification={"intent": "submission_requirements", "confidence": 0.9},
        routing={"lane": lane},
        received_at=received_at,
    )


@pytest_asyncio.fixture
async def ctx():
    """In-memory DB seeded so the faq emails fall outside the newest-20 window."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as session:
        # FAQ emails first: OLDER timestamps => sorted last (newest-first order),
        # so a limit=20 lane=None page never contains them.
        for i in range(_N_FAQ):
            session.add(_email(i, "faq", _BASE_TIME + timedelta(minutes=i)))
        # Human-review emails: NEWER timestamps => they fill the first page.
        for i in range(_N_HUMAN_REVIEW):
            session.add(
                _email(100 + i, "human_review", _BASE_TIME + timedelta(hours=1, minutes=i))
            )
        await session.commit()

    async def _override_get_db():
        async with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(client=client)

    main.app.dependency_overrides.clear()
    await engine.dispose()


async def test_lane_faq_total_and_rows_complete_beyond_first_page(ctx):
    """lane=faq returns the TRUE faq total and ALL faq rows, even though they sit
    outside the default (newest-20) window."""
    resp = await ctx.client.get("/api/v1/emails/queue", params={"lane": "faq"})
    assert resp.status_code == 200
    body = resp.json()

    # The stat: accurate lane count, not the whole table, not a page length.
    assert body["total"] == _N_FAQ

    # The table: every faq email is present and every returned row is faq.
    assert len(body["emails"]) == _N_FAQ
    assert all(e["routing"]["lane"] == "faq" for e in body["emails"])
    subjects = {e["subject"] for e in body["emails"]}
    assert subjects == {f"faq email {i}" for i in range(_N_FAQ)}


async def test_generic_page_drops_faq_documenting_the_old_bug(ctx):
    """The old path (no lane filter, default limit) would show ZERO faq emails on
    the first page — exactly the bug. total is the whole table there, not faq."""
    resp = await ctx.client.get("/api/v1/emails/queue")
    assert resp.status_code == 200
    body = resp.json()

    assert body["total"] == _N_HUMAN_REVIEW + _N_FAQ  # 27, whole table
    assert len(body["emails"]) == 20  # capped at the default page size
    faq_on_first_page = [e for e in body["emails"] if e["routing"]["lane"] == "faq"]
    assert faq_on_first_page == []  # <-- what made the old page show "0/wrong"


async def test_lane_faq_pagination_keeps_total_stable(ctx):
    """Paginating within the lane returns page-sized slices while total stays the
    lane's true count (proves it is paginated, not just a raised limit)."""
    page1 = (await ctx.client.get(
        "/api/v1/emails/queue", params={"lane": "faq", "limit": 2, "offset": 0}
    )).json()
    assert page1["total"] == _N_FAQ
    assert len(page1["emails"]) == 2

    page3 = (await ctx.client.get(
        "/api/v1/emails/queue", params={"lane": "faq", "limit": 2, "offset": 4}
    )).json()
    assert page3["total"] == _N_FAQ
    assert len(page3["emails"]) == 1  # 5 faq total, offset 4 => 1 left

    # No overlap between pages (distinct rows across the paginated slices).
    ids_p1 = {e["id"] for e in page1["emails"]}
    ids_p3 = {e["id"] for e in page3["emails"]}
    assert ids_p1.isdisjoint(ids_p3)
