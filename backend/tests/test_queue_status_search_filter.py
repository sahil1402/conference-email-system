"""Regression tests for server-side queue filtering by status / search / unassigned.

Companion to test_queue_lane_filter.py and test_chair_surface_counts.py. Before
the fix the queue page filtered a capped 20-row generic page client-side, so any
matching email outside the newest page was dropped from both the list and the
count. These assert the /queue endpoint filters server-side and returns the full
matching set + an accurate total.

The matching (old) rows are seeded OUTSIDE the newest-20 window (older
timestamps, lower ids), so a page-derived filter would report 0.
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
_N_RECENT = 22   # newest page: status DRAFT_GENERATED, assigned chair, plain subject
_N_OLD = 3       # out-of-window: status ROUTED, unassigned, distinctive subject
_FLAG = "ZZFLAG"


@pytest_asyncio.fixture
async def ctx():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as session:
        # OLD, out-of-window rows: ROUTED, unassigned, distinctive subject word.
        for i in range(_N_OLD):
            session.add(
                Email(
                    sender=f"old{i}@univ.edu",
                    subject=f"{_FLAG} old email {i}",
                    body="body",
                    status="ROUTED",
                    classification={"intent": "general_inquiry", "confidence": 0.4},
                    routing={"lane": "human_review"},
                    assigned_chair_id=None,
                    received_at=_BASE_TIME + timedelta(minutes=i),
                )
            )
        # RECENT rows fill the newest page: DRAFT_GENERATED, assigned, plain subject.
        for i in range(_N_RECENT):
            session.add(
                Email(
                    sender=f"user{i}@univ.edu",
                    subject=f"recent email {i}",
                    body="body",
                    status="DRAFT_GENERATED",
                    classification={"intent": "submission_deadline", "confidence": 0.95},
                    routing={"lane": "human_review"},
                    assigned_chair_id=1,
                    received_at=_BASE_TIME + timedelta(hours=1, minutes=i),
                )
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


async def _get(client, **params):
    resp = await client.get("/api/v1/emails/queue", params=params)
    assert resp.status_code == 200
    return resp.json()


async def test_status_filter_returns_out_of_window_rows(ctx):
    body = await _get(ctx.client, status="ROUTED")
    assert body["total"] == _N_OLD
    assert len(body["emails"]) == _N_OLD
    assert all(e["status"] == "ROUTED" for e in body["emails"])


async def test_search_matches_subject_case_insensitively(ctx):
    upper = await _get(ctx.client, search=_FLAG)
    lower = await _get(ctx.client, search=_FLAG.lower())
    assert upper["total"] == _N_OLD
    assert lower["total"] == _N_OLD  # case-insensitive
    assert all(_FLAG in e["subject"] for e in upper["emails"])


async def test_unassigned_filter_returns_out_of_window_rows(ctx):
    body = await _get(ctx.client, unassigned="true")
    assert body["total"] == _N_OLD
    assert all(e["assigned_chair_id"] is None for e in body["emails"])


async def test_combined_status_and_search(ctx):
    body = await _get(ctx.client, status="ROUTED", search=_FLAG)
    assert body["total"] == _N_OLD
    assert len(body["emails"]) == _N_OLD


async def test_generic_page_drops_all_matches_documenting_the_bug(ctx):
    """Default page (no filter, limit 20) contains none of the out-of-window
    rows — so client-side filtering of that page returned 0 for status/search/
    unassigned."""
    body = await _get(ctx.client)
    assert len(body["emails"]) == 20
    assert [e for e in body["emails"] if e["status"] == "ROUTED"] == []
    assert [e for e in body["emails"] if _FLAG in e["subject"]] == []
    assert [e for e in body["emails"] if e["assigned_chair_id"] is None] == []
