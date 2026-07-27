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
# Ticket ids for the 3 old rows. 21567/21568 share the "215" prefix; 30001 does
# not — so a "215" search proves substring (not exact-int) matching. None of
# these digit-strings appears in any seeded subject/sender.
_OLD_TICKET_IDS = (21567, 21568, 30001)


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
                    classification={"intent": "cms_support", "confidence": 0.4},
                    routing={"lane": "human_review"},
                    assigned_chair_id=None,
                    received_at=_BASE_TIME + timedelta(minutes=i),
                    source="zendesk",
                    zendesk_ticket_id=_OLD_TICKET_IDS[i],
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
                    classification={"intent": "submission_requirements", "confidence": 0.95},
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


async def test_search_matches_sender_case_insensitively(ctx):
    # "old0" is in the sender (old0@univ.edu) but NOT the subject
    # ("ZZFLAG old email 0" has no contiguous "old0"), so this isolates SENDER
    # matching — the case discovery flagged as previously untested.
    lower = await _get(ctx.client, search="old0")
    upper = await _get(ctx.client, search="OLD0")
    assert lower["total"] == 1
    assert upper["total"] == 1  # case-insensitive
    assert "old0" in lower["emails"][0]["sender"]


async def test_search_matches_full_ticket_id(ctx):
    body = await _get(ctx.client, search="21567")
    assert body["total"] == 1
    assert body["emails"][0]["zendesk_ticket_id"] == 21567


async def test_search_matches_partial_ticket_id_substring(ctx):
    # "215" is a substring of 21567 and 21568 but not 30001 — proves the
    # cast-to-text ILIKE does substring matching, not exact-int matching.
    body = await _get(ctx.client, search="215")
    assert body["total"] == 2
    assert sorted(e["zendesk_ticket_id"] for e in body["emails"]) == [21567, 21568]


async def test_search_ticket_id_ignores_leading_hash(ctx):
    plain = await _get(ctx.client, search="21567")
    hashed = await _get(ctx.client, search="#21567")
    assert hashed["total"] == plain["total"] == 1
    assert hashed["emails"][0]["zendesk_ticket_id"] == 21567


async def test_combined_status_and_ticket_id_search(ctx):
    # The ticketed rows are ROUTED; status + ticket-id compose to narrow correctly.
    match = await _get(ctx.client, status="ROUTED", search="21567")
    assert match["total"] == 1
    assert match["emails"][0]["zendesk_ticket_id"] == 21567
    # A status the ticketed row doesn't have → no match, proving composition.
    none = await _get(ctx.client, status="DRAFT_GENERATED", search="21567")
    assert none["total"] == 0


async def test_recent_rows_have_null_ticket_id_and_never_error(ctx):
    # The 22 recent rows have NULL zendesk_ticket_id; a ticket-id search must not
    # error on them (NULL casts to NULL → excluded), and they must not match.
    body = await _get(ctx.client, search="21567")
    assert body["total"] == 1
    assert all(e["zendesk_ticket_id"] == 21567 for e in body["emails"])


# --- Pagination (limit/offset) on top of an active filter -------------------
# offset/limit is the canonical pagination; a page-based UI maps page N →
# offset=(N-1)*limit and page count = ceil(total/limit). These prove the slice
# and the total are BOTH scoped to the active filter, at any offset.


async def test_pagination_slices_a_filtered_set_with_accurate_total(ctx):
    # DRAFT_GENERATED = the RECENT set (_N_RECENT = 22). Page it 10 at a time.
    p1 = await _get(ctx.client, status="DRAFT_GENERATED", limit=10, offset=0)
    p2 = await _get(ctx.client, status="DRAFT_GENERATED", limit=10, offset=10)
    p3 = await _get(ctx.client, status="DRAFT_GENERATED", limit=10, offset=20)

    # total is the filtered set size on EVERY page — independent of limit/offset.
    assert p1["total"] == p2["total"] == p3["total"] == _N_RECENT

    # Slice sizes: 10 + 10 + 2 = 22.
    assert len(p1["emails"]) == 10
    assert len(p2["emails"]) == 10
    assert len(p3["emails"]) == _N_RECENT - 20

    # The filter applies to every page, not just the first.
    for page in (p1, p2, p3):
        assert all(e["status"] == "DRAFT_GENERATED" for e in page["emails"])

    # Pages are disjoint and together cover the whole filtered set exactly once.
    ids = [e["id"] for e in p1["emails"] + p2["emails"] + p3["emails"]]
    assert len(ids) == _N_RECENT
    assert len(set(ids)) == _N_RECENT  # no row repeated across pages
    full = await _get(ctx.client, status="DRAFT_GENERATED", limit=100, offset=0)
    assert set(ids) == {e["id"] for e in full["emails"]}


async def test_pagination_total_reflects_filtered_set_not_whole_table(ctx):
    # ROUTED = the 3-row OLD set. Even paging 1 at a time, total stays 3 — the
    # filtered size (page count = ceil(3/1) = 3), NOT the 25-row table.
    first = await _get(ctx.client, status="ROUTED", limit=1, offset=0)
    assert first["total"] == _N_OLD  # not _N_OLD + _N_RECENT
    assert len(first["emails"]) == 1

    last = await _get(ctx.client, status="ROUTED", limit=1, offset=_N_OLD - 1)
    assert len(last["emails"]) == 1

    # Past the last page: empty slice, but the total is unchanged.
    past_end = await _get(ctx.client, status="ROUTED", limit=1, offset=_N_OLD)
    assert past_end["emails"] == []
    assert past_end["total"] == _N_OLD


async def test_pagination_composes_with_search(ctx):
    # search=_FLAG matches the 3 OLD rows; page them 2 at a time.
    p1 = await _get(ctx.client, search=_FLAG, limit=2, offset=0)
    p2 = await _get(ctx.client, search=_FLAG, limit=2, offset=2)
    assert p1["total"] == p2["total"] == _N_OLD  # 3
    assert len(p1["emails"]) == 2
    assert len(p2["emails"]) == 1
    ids = {e["id"] for e in p1["emails"] + p2["emails"]}
    assert len(ids) == _N_OLD  # disjoint, covers the filtered set


async def test_pagination_rejects_out_of_range_params(ctx):
    # limit is bounded 1..200 and offset >= 0 — FastAPI 422s rather than silently
    # clamping, so a caller can't request an unbounded page or a negative offset.
    assert (
        await ctx.client.get("/api/v1/emails/queue", params={"limit": 201})
    ).status_code == 422
    assert (
        await ctx.client.get("/api/v1/emails/queue", params={"limit": 0})
    ).status_code == 422
    assert (
        await ctx.client.get("/api/v1/emails/queue", params={"offset": -1})
    ).status_code == 422


async def test_pagination_accepts_boundary_params(ctx):
    for params in ({"limit": 1}, {"limit": 200}, {"offset": 0}):
        resp = await ctx.client.get("/api/v1/emails/queue", params=params)
        assert resp.status_code == 200, params
