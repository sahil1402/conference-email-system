"""Date-range filtering on GET /emails/queue and /emails/queue/facets.

Covers the `received_after` / `received_before` contract added on top of the
repository's inclusive range: a BARE DATE covers the whole day (`received_before`
widens to 23:59:59.999999), an explicit timestamp is used verbatim, an inverted
or malformed range is a 422, and both endpoints resolve a window identically.

The discriminating fixture is a ticket at **23:50** on the boundary day. Under
the naive coercion this contract exists to prevent (`received_before=2026-01-20`
-> midnight), that ticket disappears from its own day. Every whole-day assertion
below is written so it FAILS in that world rather than passing vacuously.

SCOPE LIMIT — runs on in-memory SQLite via ASGITransport. SQLite stores DATETIME
without tzinfo, so values read back are naive UTC; the API layer is what attaches
UTC, and that is asserted through the API's own echoed `page_info`, not by
inspecting stored rows.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from app.db.database import get_db
from app.db.models import Base, Email

QUEUE = "/api/v1/emails/queue"
FACETS = "/api/v1/emails/queue/facets"

# (subject, received_at, source, zendesk_status) — the 23:50 row is the one that
# vanishes under midnight coercion.
FIXTURE_ROWS = [
    ("jan19-late", datetime(2026, 1, 19, 23, 59, tzinfo=timezone.utc), "zendesk", "open"),
    ("jan20-midnight", datetime(2026, 1, 20, 0, 0, tzinfo=timezone.utc), "zendesk", "open"),
    ("jan20-midday", datetime(2026, 1, 20, 12, 30, tzinfo=timezone.utc), "zendesk", "new"),
    ("jan20-2350", datetime(2026, 1, 20, 23, 50, tzinfo=timezone.utc), "toy_dataset", None),
    ("jan21-morning", datetime(2026, 1, 21, 9, 0, tzinfo=timezone.utc), "zendesk", "solved"),
]


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
        async with factory() as session:
            yield session

    async with factory() as session:
        for idx, (subject, received, source, zstatus) in enumerate(FIXTURE_ROWS):
            session.add(
                Email(
                    sender=f"author{idx}@uni.edu",
                    subject=subject,
                    body="body",
                    status="DRAFT_GENERATED",
                    routing={"lane": "faq"},
                    source=source,
                    zendesk_status=zstatus,
                    zendesk_ticket_id=(5000 + idx) if zstatus else None,
                    received_at=received,
                )
            )
        await session.commit()

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    main.app.dependency_overrides.clear()
    await engine.dispose()


async def _subjects(client, query: str) -> list[str]:
    response = await client.get(QUEUE + query)
    assert response.status_code == 200, response.text
    return [e["subject"] for e in response.json()["emails"]]


# --- 1. bare date covers the WHOLE day ------------------------------------


async def test_bare_date_same_day_includes_end_of_day_ticket(client):
    """`received_before=2026-01-20` must include a 23:50 ticket on that day.

    THE regression this contract exists for: coercing a bare date to midnight
    makes an inclusive `<=` exclude all but the first instant of the day, so
    this returns 1 row instead of 3 — silently losing almost a full day.
    """
    subjects = await _subjects(
        client, "?received_after=2026-01-20&received_before=2026-01-20"
    )
    assert sorted(subjects) == ["jan20-2350", "jan20-midday", "jan20-midnight"]
    # Stated separately so the intent survives a fixture change: the LAST
    # moment of the day is in range, which is exactly what midnight coercion drops.
    assert "jan20-2350" in subjects


async def test_bare_date_excludes_adjacent_days(client):
    """The whole-day widening must not bleed into the next or previous day."""
    subjects = await _subjects(
        client, "?received_after=2026-01-20&received_before=2026-01-20"
    )
    assert "jan19-late" not in subjects
    assert "jan21-morning" not in subjects


async def test_bare_date_received_after_starts_at_midnight(client):
    """`received_after` takes the START of its day, so 00:00 is included."""
    subjects = await _subjects(client, "?received_after=2026-01-20")
    assert "jan20-midnight" in subjects
    assert "jan19-late" not in subjects


# --- 2. an explicit timestamp is NOT widened ------------------------------


async def test_explicit_timestamp_is_not_widened(client):
    """An explicit midnight means midnight — never the end of that day.

    Asserted as a strict comparison against the bare-date result for the SAME
    day, so the test states the actual distinction (verbatim vs widened) rather
    than a row count that a fixture edit could accidentally satisfy. Both queries
    pin the SAME `received_after`, so the upper bound is the only variable.
    """
    explicit = await _subjects(
        client, "?received_after=2026-01-20&received_before=2026-01-20T00:00:00"
    )
    bare = await _subjects(
        client, "?received_after=2026-01-20&received_before=2026-01-20"
    )

    assert explicit == ["jan20-midnight"]
    assert set(explicit) < set(bare), "explicit midnight must be a strict subset"
    assert "jan20-2350" in bare and "jan20-2350" not in explicit


async def test_explicit_timestamp_with_offset_is_respected(client):
    """A timestamp carrying an offset is converted, not treated as UTC wall time.

    13:30-05:00 is 18:30Z, so the 12:30Z ticket is in range and 23:50Z is not.
    """
    subjects = await _subjects(client, "?received_before=2026-01-20T13:30:00-05:00")
    assert "jan20-midday" in subjects
    assert "jan20-2350" not in subjects


# --- 3 & 4. validation ----------------------------------------------------


async def test_inverted_range_returns_422_naming_both_values(client):
    """An inverted range is an error, not an empty page.

    Without this the caller cannot tell "you typed the dates backwards" from
    "there are genuinely no tickets in this window".
    """
    response = await client.get(
        QUEUE + "?received_after=2026-01-21&received_before=2026-01-20"
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "received_after" in detail and "received_before" in detail
    # Both RESOLVED values appear, so the message shows the end-of-day expansion
    # that produced the comparison rather than just echoing the raw input.
    assert "2026-01-21T00:00:00" in detail
    assert "2026-01-20T23:59:59.999999" in detail


async def test_equal_bounds_are_valid_not_inverted(client):
    """after == before is a legal (single-day) window, not a 422."""
    response = await client.get(
        QUEUE + "?received_after=2026-01-20&received_before=2026-01-20"
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "query",
    [
        "?received_after=not-a-date",
        "?received_before=not-a-date",
        "?received_after=20-01-2026",
        "?received_before=2026-13-45",
    ],
)
async def test_malformed_date_returns_422_with_format_hint(client, query):
    """A mistyped date errors — it never degrades to an unfiltered queue.

    Silently ignoring it would show the chair the WHOLE queue while they believe
    it is filtered, which is worse than an error.
    """
    response = await client.get(QUEUE + query)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "YYYY-MM-DD" in detail, detail
    assert "ISO-8601" in detail, detail


# --- 5. blank is absent ---------------------------------------------------


@pytest.mark.parametrize(
    "query", ["?received_after=", "?received_before=", "?received_after=&received_before="]
)
async def test_blank_param_is_treated_as_absent(client, query):
    """An empty value applies no filter (200) rather than erroring."""
    response = await client.get(QUEUE + query)
    assert response.status_code == 200
    assert response.json()["total"] == len(FIXTURE_ROWS)


async def test_no_params_is_unchanged(client):
    """The baseline: absent params leave the queue exactly as before."""
    response = await client.get(QUEUE)
    assert response.status_code == 200
    assert response.json()["total"] == len(FIXTURE_ROWS)


# --- 6. naive input is UTC ------------------------------------------------


async def test_naive_input_is_interpreted_as_utc(client):
    """A timestamp with no offset means UTC, matching the ingest convention."""
    response = await client.get(QUEUE + "?received_after=2026-01-20T12:00:00")
    assert response.status_code == 200
    echoed = response.json()["page_info"]["received_after"]
    # Serialized with an explicit UTC designator, so the resolved instant is
    # unambiguous to any client reading page_info.
    assert echoed.startswith("2026-01-20T12:00:00")
    assert echoed.endswith("Z") or "+00:00" in echoed

    subjects = [e["subject"] for e in response.json()["emails"]]
    assert "jan20-midday" in subjects  # 12:30Z is after 12:00Z
    assert "jan20-midnight" not in subjects


async def test_page_info_echoes_the_resolved_window(client):
    """page_info reports the RESOLVED bounds, incl. end-of-day expansion."""
    response = await client.get(
        QUEUE + "?received_after=2026-01-20&received_before=2026-01-20"
    )
    info = response.json()["page_info"]
    assert info["received_after"].startswith("2026-01-20T00:00:00")
    assert info["received_before"].startswith("2026-01-20T23:59:59.999999")


# --- 7. /queue and /queue/facets resolve the SAME window ------------------


@pytest.mark.parametrize(
    "query",
    [
        "?received_after=2026-01-20&received_before=2026-01-20",
        "?received_before=2026-01-20",
        "?received_after=2026-01-20T12:00:00",
        "?received_after=2026-01-19&received_before=2026-01-21",
    ],
    ids=["same-day", "before-only", "after-timestamp", "multi-day"],
)
async def test_queue_and_facets_resolve_the_same_window(client, query):
    """Both endpoints must agree on the window, or the counts beside the list
    would describe a different set than the list itself.

    NOTE: /queue/facets does not return page_info, so this cannot compare echoed
    bounds directly (as originally scoped). It asserts the stronger observable
    property instead: for the same query, each source's facet count equals the
    /queue total when filtered to that source. A divergent expansion in either
    endpoint breaks this equality.
    """
    facets = await client.get(FACETS + query)
    assert facets.status_code == 200, facets.text
    by_source = facets.json()["by_source"]

    for source in ("zendesk", "toy_dataset"):
        scoped = await client.get(f"{QUEUE}{query}&source={source}")
        assert scoped.status_code == 200, scoped.text
        assert by_source.get(source, 0) == scoped.json()["total"], (
            f"{source}: facets and queue disagree for {query}"
        )


async def test_facets_and_queue_agree_on_the_end_of_day_boundary(client):
    """The equivalence above, aimed at the exact row midnight coercion drops.

    The 23:50 ticket is the only `toy_dataset` row, so if the facets endpoint
    widened the day differently from /queue, its toy_dataset count would be 0
    while /queue returned 1.
    """
    query = "?received_after=2026-01-20&received_before=2026-01-20"
    facets = (await client.get(FACETS + query)).json()
    queue_total = (await client.get(f"{QUEUE}{query}&source=toy_dataset")).json()["total"]
    assert facets["by_source"].get("toy_dataset") == 1
    assert queue_total == 1


async def test_facets_reject_an_inverted_range_too(client):
    """Validation is shared, so the facets endpoint rejects identically."""
    response = await client.get(
        FACETS + "?received_after=2026-01-21&received_before=2026-01-20"
    )
    assert response.status_code == 422


# --- 8. facet narrowing vs whole-table `sources` --------------------------


async def test_date_filter_narrows_facet_counts(client):
    """A date window narrows by_zendesk_status and by_source (context filter)."""
    unfiltered = (await client.get(FACETS)).json()
    assert unfiltered["by_source"] == {"zendesk": 4, "toy_dataset": 1}
    assert unfiltered["by_zendesk_status"] == {"open": 2, "new": 1, "solved": 1}

    scoped = (
        await client.get(FACETS + "?received_after=2026-01-20&received_before=2026-01-20")
    ).json()
    # Jan 20 holds 2 zendesk rows (midnight/open, midday/new) + 1 toy (23:50).
    assert scoped["by_source"] == {"zendesk": 2, "toy_dataset": 1}
    assert scoped["by_zendesk_status"] == {"open": 1, "new": 1}
    assert "solved" not in scoped["by_zendesk_status"]


async def test_sources_list_stays_whole_table_under_a_date_filter(client):
    """`sources` is the WHOLE-table distinct list, deliberately not narrowed.

    It drives the self-hiding source toggle, which must reflect what exists in
    the data — not the current window. A date range that happens to contain one
    source must not make the toggle vanish.
    """
    narrow = (
        await client.get(FACETS + "?received_after=2026-01-21&received_before=2026-01-21")
    ).json()
    # That window holds ONLY a zendesk row...
    assert narrow["by_source"] == {"zendesk": 1}
    # ...yet both sources remain listed.
    assert narrow["sources"] == ["toy_dataset", "zendesk"]


async def test_date_filter_composes_with_other_queue_filters(client):
    """The range ANDs with existing filters rather than replacing them."""
    subjects = await _subjects(
        client,
        "?received_after=2026-01-19&received_before=2026-01-21&source=zendesk&zendesk_status=open",
    )
    assert sorted(subjects) == ["jan19-late", "jan20-midnight"]


async def test_queue_total_matches_returned_rows_under_a_date_filter(client):
    """`total` and the page come from the same conditions — they cannot disagree."""
    response = await client.get(
        QUEUE + "?received_after=2026-01-20&received_before=2026-01-20&limit=200"
    )
    body = response.json()
    assert body["total"] == len(body["emails"]) == 3
