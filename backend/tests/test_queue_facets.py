"""Tests for the queue facets aggregate + the source / zendesk_status filters.

Companion to test_queue_status_search_filter.py and test_chair_surface_counts.py.
These cover the "Queue status bar + self-hiding source toggle" work:

  - GET /emails/queue/facets is a DEDICATED grouped aggregate (by zendesk_status
    and by source) over the whole matching set — not a tally over a capped page
    (the Phase 6C bug class). Rows are seeded OUTSIDE the newest-20 window so a
    page-derived count would be wrong.
  - GET /emails/queue?source=... and ?zendesk_status=... filter server-side and
    compose with the existing lane filter.
  - `sources` reflects the DISTINCT sources actually present (drives the
    self-hiding toggle): one source → length 1, both → length 2.
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

# Zendesk rows, seeded OLD (out of the newest-20 window) so a page-derived facet
# count would drop them. Mix of zendesk_status values and lanes.
#   new: 3 (2 human_review, 1 faq) · open: 2 (human_review) · solved: 1 (faq)
_ZENDESK_ROWS = [
    ("new", "human_review"),
    ("new", "human_review"),
    ("new", "faq"),
    ("open", "human_review"),
    ("open", "human_review"),
    ("solved", "faq"),
]
# Toy-dataset rows, seeded NEWER so they fill the first default page.
_N_TOY = 22


def _zendesk_email(idx: int, zstatus: str, lane: str, received_at: datetime) -> Email:
    return Email(
        sender=f"z{idx}@univ.edu",
        subject=f"zendesk ticket {idx}",
        body="body",
        status="DRAFT_GENERATED",
        classification={"intent": "review_assignment", "confidence": 0.9},
        routing={"lane": lane},
        assigned_chair_id=1 if lane == "human_review" else None,
        received_at=received_at,
        source="zendesk",
        zendesk_ticket_id=1000 + idx,
        zendesk_status=zstatus,
    )


def _toy_email(idx: int, received_at: datetime) -> Email:
    return Email(
        sender=f"t{idx}@univ.edu",
        subject=f"toy email {idx}",
        body="body",
        status="DRAFT_GENERATED",
        classification={"intent": "submission_deadline", "confidence": 0.95},
        routing={"lane": "human_review"},
        assigned_chair_id=1,
        received_at=received_at,
        source="toy_dataset",
    )


async def _make_ctx(*, seed):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        await seed(session)
        await session.commit()

    async def _override_get_db():
        async with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    return SimpleNamespace(client=client, engine=engine)


async def _seed_both(session):
    # Zendesk rows OLD (out of window).
    for i, (zstatus, lane) in enumerate(_ZENDESK_ROWS):
        session.add(_zendesk_email(i, zstatus, lane, _BASE_TIME + timedelta(minutes=i)))
    # Toy rows NEWER (fill the first page).
    for i in range(_N_TOY):
        session.add(_toy_email(i, _BASE_TIME + timedelta(hours=1, minutes=i)))


@pytest_asyncio.fixture
async def ctx():
    """Both sources present, Zendesk rows out of the newest-20 window."""
    c = await _make_ctx(seed=_seed_both)
    yield c
    await c.client.aclose()
    main.app.dependency_overrides.clear()
    await c.engine.dispose()


@pytest_asyncio.fixture
async def ctx_single_source():
    """Only toy_dataset present — the self-hide (one distinct source) scenario."""

    async def _seed(session):
        for i in range(3):
            session.add(_toy_email(i, _BASE_TIME + timedelta(minutes=i)))

    c = await _make_ctx(seed=_seed)
    yield c
    await c.client.aclose()
    main.app.dependency_overrides.clear()
    await c.engine.dispose()


async def _facets(client, **params):
    resp = await client.get("/api/v1/emails/queue/facets", params=params)
    assert resp.status_code == 200
    return resp.json()


# --- Facets: by_zendesk_status --------------------------------------------
async def test_facets_by_zendesk_status_counts_out_of_window_rows(ctx):
    """Grouped over the WHOLE table, so the old out-of-window Zendesk rows are
    counted — a capped-page tally would have missed them (they sit behind 22
    newer toy rows)."""
    body = await _facets(ctx.client)
    assert body["by_zendesk_status"] == {"new": 3, "open": 2, "solved": 1}


async def test_facets_by_source_counts(ctx):
    body = await _facets(ctx.client)
    assert body["by_source"] == {"zendesk": 6, "toy_dataset": 22}


async def test_facets_sources_lists_both_distinct(ctx):
    body = await _facets(ctx.client)
    assert body["sources"] == ["toy_dataset", "zendesk"]


async def test_facets_compose_with_lane_context(ctx):
    """The status bar counts respect the active lane filter (compose): scoping to
    human_review drops the faq-lane Zendesk rows (1 new + 1 solved)."""
    body = await _facets(ctx.client, lane="human_review")
    # new: 2 human_review (the 3rd new is faq) · open: 2 · solved: 0 (was faq → gone)
    assert body["by_zendesk_status"] == {"new": 2, "open": 2}


async def test_facets_single_source_hides_toggle(ctx_single_source):
    """Only one distinct source → the frontend hides the toggle; no meaningful
    zendesk_status counts exist either."""
    body = await _facets(ctx_single_source.client)
    assert body["sources"] == ["toy_dataset"]
    assert len(body["sources"]) == 1
    assert body["by_zendesk_status"] == {}


# --- Queue filters: source / zendesk_status --------------------------------
async def test_queue_filter_by_zendesk_status(ctx):
    """?zendesk_status=new returns exactly the 3 new Zendesk rows (full slice +
    accurate total), even though they are out of the default window."""
    resp = await ctx.client.get(
        "/api/v1/emails/queue", params={"zendesk_status": "new", "limit": 200}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["emails"]) == 3
    assert all(e["zendesk_status"] == "new" for e in body["emails"])


async def test_queue_filter_by_source(ctx):
    resp = await ctx.client.get(
        "/api/v1/emails/queue", params={"source": "zendesk", "limit": 200}
    )
    body = resp.json()
    assert body["total"] == 6
    assert all(e["source"] == "zendesk" for e in body["emails"])


async def test_queue_zendesk_status_composes_with_lane(ctx):
    """The core composition requirement: Zendesk status: open AND lane: review
    together — both open rows are human_review, so both survive."""
    resp = await ctx.client.get(
        "/api/v1/emails/queue",
        params={"zendesk_status": "open", "lane": "human_review", "limit": 200},
    )
    body = resp.json()
    assert body["total"] == 2
    assert all(
        e["zendesk_status"] == "open" and e["routing"]["lane"] == "human_review"
        for e in body["emails"]
    )


async def test_email_dict_exposes_source_and_zendesk_status(ctx):
    """Queue rows carry source + zendesk_status so the UI can badge / gate on them."""
    body = (await ctx.client.get("/api/v1/emails/queue", params={"limit": 200})).json()
    by_source = {e["source"] for e in body["emails"]}
    assert by_source == {"zendesk", "toy_dataset"}
    # Toy rows have no zendesk_status; Zendesk rows do.
    for e in body["emails"]:
        if e["source"] == "toy_dataset":
            assert e["zendesk_status"] is None
        else:
            assert e["zendesk_status"] in {"new", "open", "solved"}


# --- Solved/closed bucketing (Piece A3) ------------------------------------
# "solved" is the combined solved+closed bucket alias (see
# EmailRepository._SOLVED_BUCKET_*). These use their own seed so the exact-count
# assertions above stay intact.
#   open: 1 (human_review) · solved: 2 (1 faq, 1 human_review)
#   closed: 3 (1 faq, 2 human_review)
_BUCKET_ROWS = [
    ("open", "human_review"),
    ("solved", "faq"),
    ("solved", "human_review"),
    ("closed", "faq"),
    ("closed", "human_review"),
    ("closed", "human_review"),
]


async def _seed_bucket(session):
    for i, (zstatus, lane) in enumerate(_BUCKET_ROWS):
        session.add(_zendesk_email(i, zstatus, lane, _BASE_TIME + timedelta(minutes=i)))


@pytest_asyncio.fixture
async def ctx_bucket():
    """Zendesk rows spanning open / solved / closed for bucket-filter tests."""
    c = await _make_ctx(seed=_seed_bucket)
    yield c
    await c.client.aclose()
    main.app.dependency_overrides.clear()
    await c.engine.dispose()


@pytest_asyncio.fixture
async def ctx_closed_only():
    """Only closed rows — verifies closed folds into the solved bucket even with
    no strictly-solved row present."""

    async def _seed(session):
        for i in range(2):
            session.add(
                _zendesk_email(i, "closed", "human_review", _BASE_TIME + timedelta(minutes=i))
            )

    c = await _make_ctx(seed=_seed)
    yield c
    await c.client.aclose()
    main.app.dependency_overrides.clear()
    await c.engine.dispose()


async def test_queue_solved_bucket_returns_solved_and_closed(ctx_bucket):
    """Filtering by the "solved" bucket returns BOTH solved and closed rows."""
    resp = await ctx_bucket.client.get(
        "/api/v1/emails/queue", params={"zendesk_status": "solved", "limit": 200}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5  # 2 solved + 3 closed
    assert len(body["emails"]) == 5
    assert {e["zendesk_status"] for e in body["emails"]} == {"solved", "closed"}


async def test_queue_open_still_exact_match(ctx_bucket):
    """A non-merged status (open) is unchanged — exact-match, excludes solved/closed."""
    resp = await ctx_bucket.client.get(
        "/api/v1/emails/queue", params={"zendesk_status": "open", "limit": 200}
    )
    body = resp.json()
    assert body["total"] == 1
    assert all(e["zendesk_status"] == "open" for e in body["emails"])


async def test_queue_solved_bucket_composes_with_lane(ctx_bucket):
    """The bucket filter still composes with lane: human_review keeps 1 solved +
    2 closed (the faq solved + faq closed are dropped)."""
    resp = await ctx_bucket.client.get(
        "/api/v1/emails/queue",
        params={"zendesk_status": "solved", "lane": "human_review", "limit": 200},
    )
    body = resp.json()
    assert body["total"] == 3
    assert all(
        e["zendesk_status"] in {"solved", "closed"}
        and e["routing"]["lane"] == "human_review"
        for e in body["emails"]
    )


async def test_facets_merge_solved_and_closed(ctx_bucket):
    """Facets show ONE combined solved entry (2 solved + 3 closed = 5); "closed"
    is never its own row."""
    body = await _facets(ctx_bucket.client)
    assert "closed" not in body["by_zendesk_status"]
    assert body["by_zendesk_status"] == {"open": 1, "solved": 5}


async def test_facets_closed_only_folds_into_solved(ctx_closed_only):
    """With only closed rows and no strictly-solved row, closed still surfaces
    under the "solved" bucket key."""
    body = await _facets(ctx_closed_only.client)
    assert body["by_zendesk_status"] == {"solved": 2}
