"""Regression tests for the two per-chair surfaces (analytics chart + queue filter).

Both previously derived their numbers from a capped generic queue page
(GET /queue, limit=20), so a chair whose emails fell outside the newest page
showed 0 — even though the emails existed. These guard the fixed data sources:

  1. Analytics "Email Volume per Chair"  → GET /analytics/summary.chair_distribution
     (a grouped aggregate over ALL emails, not a page).
  2. Queue chair filter                  → GET /queue?chair_id=N
     (server-side chair filter with a chair-scoped total, not a client slice).

The seed puts the target chair's emails OUTSIDE the newest-20 window (older
timestamps, lower ids) so the old capped-page approach would report 0.
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
_TARGET_CHAIR = 3          # e.g. "Local Arrangements Chair"
_TARGET_COUNT = 2          # its two real emails (like ids 41, 42)
_BIG_CHAIR = 1             # a chair with more emails than one default page
_BIG_COUNT = 22            # > default limit of 20


def _email(idx: int, chair_id: int, received_at: datetime) -> Email:
    return Email(
        sender=f"user{idx}@univ.edu",
        subject=f"chair{chair_id} email {idx}",
        body="body",
        status="DRAFT_GENERATED",
        classification={"intent": "reviewer_assignment", "confidence": 0.9},
        routing={"lane": "human_review"},
        assigned_chair_id=chair_id,
        received_at=received_at,
    )


@pytest_asyncio.fixture
async def ctx():
    """In-memory DB seeded so the target chair's emails are outside the newest page."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as session:
        # Target chair FIRST with OLD timestamps => sorted last (newest-first),
        # so a default limit=20 generic page never contains them.
        for i in range(_TARGET_COUNT):
            session.add(_email(i, _TARGET_CHAIR, _BASE_TIME + timedelta(minutes=i)))
        # A big chair with NEWER timestamps => it fills the first page and then some.
        for i in range(_BIG_COUNT):
            session.add(
                _email(100 + i, _BIG_CHAIR, _BASE_TIME + timedelta(hours=1, minutes=i))
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


# --- Surface 1: Analytics "Email Volume per Chair" -------------------------
async def test_analytics_summary_chair_distribution_counts_all_chairs(ctx):
    """chair_distribution counts every chair over the whole table — the target
    chair's out-of-window emails are counted, not dropped."""
    resp = await ctx.client.get("/api/v1/analytics/summary")
    assert resp.status_code == 200
    dist = resp.json()["chair_distribution"]

    # Keys are stringified chair ids.
    assert dist[str(_TARGET_CHAIR)] == _TARGET_COUNT  # 2, NOT 0 (the bug)
    assert dist[str(_BIG_CHAIR)] == _BIG_COUNT        # 22, full count
    # Only chairs with assignments appear; totals add up to all assigned emails.
    assert sum(dist.values()) == _TARGET_COUNT + _BIG_COUNT


# --- Surface 2: Queue filter-by-chair --------------------------------------
async def test_queue_chair_filter_returns_out_of_window_rows(ctx):
    """GET /queue?chair_id=N returns the chair's true total AND its rows even when
    they sit outside the default newest-20 page."""
    resp = await ctx.client.get(
        "/api/v1/emails/queue", params={"chair_id": _TARGET_CHAIR}
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["total"] == _TARGET_COUNT                      # accurate stat
    assert len(body["emails"]) == _TARGET_COUNT                # full slice
    assert all(e["assigned_chair_id"] == _TARGET_CHAIR for e in body["emails"])


async def test_generic_page_drops_the_target_chair_documenting_the_bug(ctx):
    """The old path (no chair filter, default limit) shows ZERO of the target
    chair on the first page — exactly the reported symptom."""
    body = (await ctx.client.get("/api/v1/emails/queue")).json()
    assert len(body["emails"]) == 20  # capped default page
    on_page = [e for e in body["emails"] if e["assigned_chair_id"] == _TARGET_CHAIR]
    assert on_page == []  # <-- why the chart/dropdown showed 0


async def test_queue_chair_filter_total_is_page_size_independent(ctx):
    """For a chair with more emails than one default page, the chair-scoped total
    is accurate and a large enough page returns all rows (what the queue's chair
    filter requests) — not a truncated 20."""
    default_page = (await ctx.client.get(
        "/api/v1/emails/queue", params={"chair_id": _BIG_CHAIR}
    )).json()
    assert default_page["total"] == _BIG_COUNT      # accurate regardless of page
    assert len(default_page["emails"]) == 20        # default page still caps rows

    full = (await ctx.client.get(
        "/api/v1/emails/queue", params={"chair_id": _BIG_CHAIR, "limit": 200}
    )).json()
    assert full["total"] == _BIG_COUNT
    assert len(full["emails"]) == _BIG_COUNT        # all rows returned
    assert all(e["assigned_chair_id"] == _BIG_CHAIR for e in full["emails"])
