"""The OpenReview/main queue split.

Emails detected as a reply to an OpenReview notification are pulled OUT of the
main queue and shown in their own at ``/queue/openreview``. THE property under
test is that the two are exact COMPLEMENTS: every email appears in exactly one,
so nothing is hidden from both and nothing is double-counted.

⚠️ The NULL cases are the point, not padding. Both endpoints filter on a value
inside the ``extraction`` JSON column, and that column is NULL for every row
processed before it existed. Under SQL three-valued logic the obvious spelling
(``== True``, which negates to ``!= 1`` / ``!= true``) evaluates to NULL for
those rows and would SILENTLY DROP every legacy email from the main queue —
verified empirically, not theorised. ``.as_boolean().is_(True)`` renders
``IS 1`` / ``IS true``, which is total. `test_legacy_null_extraction_*` is what
catches a revert.

SCOPE LIMIT: SQLite only, which is what this suite runs on. The Postgres half of
the dialect question lives in ``test_postgres_migration.py`` (skipif-gated, run
in CI) — see ``test_openreview_candidate_filter_uses_dialect_agnostic_json``.
"""

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
OR_QUEUE = "/api/v1/emails/queue/openreview"
FACETS = "/api/v1/emails/queue/facets"


def _candidate(value: bool = True) -> dict:
    """An extraction dict exactly as the pipeline persists it."""
    return {
        "submission_numbers": ["1030"],
        "openreview_forum_ids": ["ll0avn6ylq"],
        "openreview_note_id": "jnHgRMHgrm" if value else None,
        "openreview_notification_sender": (
            "aaai2027-notifications@openreview.net" if value else None
        ),
        "extracted_reply_text": "I will review before the deadline.",
        "authors": [],
        "method": "llm_distiller",
        "openreview_reply_candidate": value,
    }


# (subject, extraction, zendesk_status) — the full matrix both endpoints partition.
_ROWS = [
    ("cand-open", _candidate(True), "open"),
    ("cand-new", _candidate(True), "new"),
    ("cand-solved", _candidate(True), "solved"),
    ("cand-closed", _candidate(True), "closed"),
    ("cand-no-zendesk", _candidate(True), None),
    ("plain-open", _candidate(False), "open"),
    ("plain-solved", _candidate(False), "solved"),
    ("key-absent", {"submission_numbers": [], "method": "none"}, "open"),
    ("legacy-null-extraction", None, "open"),
    ("legacy-toy-row", None, None),
]

# Derived from _ROWS rather than written out, so the two can never disagree.
_EXPECTED_OPENREVIEW = {"cand-open", "cand-new", "cand-no-zendesk"}
_EXPECTED_MAIN = {r[0] for r in _ROWS} - _EXPECTED_OPENREVIEW


@pytest_asyncio.fixture
async def client_and_factory():
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
        for subject, extraction, zendesk_status in _ROWS:
            session.add(
                Email(
                    sender=f"{subject}@example.edu",
                    subject=subject,
                    body="b",
                    status="DRAFT_GENERATED",
                    routing={"lane": "human_review"},
                    extraction=extraction,
                    source="zendesk" if zendesk_status else "toy_dataset",
                    zendesk_ticket_id=None,
                    zendesk_status=zendesk_status,
                )
            )
        await session.commit()

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, factory
    main.app.dependency_overrides.clear()
    await engine.dispose()


async def _subjects(client, url, **params) -> list[str]:
    resp = await client.get(url, params={"limit": 200, **params})
    assert resp.status_code == 200, resp.text
    return [e["subject"] for e in resp.json()["emails"]]


# ---------------------------------------------------------------------------
# The split
# ---------------------------------------------------------------------------
async def test_unresolved_candidate_is_absent_from_the_main_queue(client_and_factory):
    client, _ = client_and_factory

    subjects = await _subjects(client, QUEUE)

    assert "cand-open" not in subjects
    assert "cand-new" not in subjects


async def test_unresolved_candidate_appears_in_the_openreview_queue(client_and_factory):
    client, _ = client_and_factory

    subjects = await _subjects(client, OR_QUEUE)

    assert "cand-open" in subjects
    assert "cand-new" in subjects


async def test_non_candidate_appears_in_the_main_queue_only(client_and_factory):
    client, _ = client_and_factory

    assert "plain-open" in await _subjects(client, QUEUE)
    assert "plain-open" not in await _subjects(client, OR_QUEUE)


async def test_the_two_queues_are_exact_complements(client_and_factory):
    """THE central property. Derived from one predicate, so an email cannot be
    hidden from both queues or counted in both."""
    client, _ = client_and_factory

    main_q = await _subjects(client, QUEUE)
    or_q = await _subjects(client, OR_QUEUE)

    assert set(main_q) == _EXPECTED_MAIN
    assert set(or_q) == _EXPECTED_OPENREVIEW
    assert not (set(main_q) & set(or_q)), "an email appears in BOTH queues"
    assert sorted(main_q + or_q) == sorted(r[0] for r in _ROWS), "an email is in NEITHER"


# ---------------------------------------------------------------------------
# Resolved candidates keep their previous visibility
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("subject", ["cand-solved", "cand-closed"])
async def test_resolved_candidate_stays_where_it_was(client_and_factory, subject):
    """A candidate already solved/closed is NOT pulled out.

    The split targets work still to be done. Moving an already-resolved ticket
    would change the visibility of something a chair had finished with — and it
    would appear somewhere it never appeared before.
    """
    client, _ = client_and_factory

    assert subject in await _subjects(client, QUEUE)
    assert subject not in await _subjects(client, OR_QUEUE)


async def test_resolved_candidate_behaves_like_any_other_resolved_email(
    client_and_factory,
):
    """Same treatment under a status filter as a resolved NON-candidate."""
    client, _ = client_and_factory

    solved = await _subjects(client, QUEUE, zendesk_status="solved")

    assert "cand-solved" in solved
    assert "cand-closed" in solved  # "solved" is the combined solved+closed bucket
    assert "plain-solved" in solved


# ---------------------------------------------------------------------------
# NULL handling — the three-valued-logic trap
# ---------------------------------------------------------------------------
async def test_legacy_null_extraction_stays_in_the_main_queue(client_and_factory):
    """⚠️ THE regression guard for the `== True` trap.

    `extraction` is NULL for every row processed before that column existed. With
    `== True` the negation renders `!= 1` / `!= true`, which is NULL for these
    rows, and they vanish from the main queue with no error — on production that
    is a large share of the inbox. `.as_boolean().is_(True)` renders `IS 1` /
    `IS true`, which is total.
    """
    client, _ = client_and_factory

    main_q = await _subjects(client, QUEUE)

    assert "legacy-null-extraction" in main_q
    assert "legacy-toy-row" in main_q


async def test_legacy_null_extraction_is_not_an_openreview_candidate(
    client_and_factory,
):
    client, _ = client_and_factory

    or_q = await _subjects(client, OR_QUEUE)

    assert "legacy-null-extraction" not in or_q
    assert "legacy-toy-row" not in or_q


async def test_extraction_without_the_key_stays_in_the_main_queue(client_and_factory):
    """An extraction that ran before the flag existed has no such key at all."""
    client, _ = client_and_factory

    assert "key-absent" in await _subjects(client, QUEUE)
    assert "key-absent" not in await _subjects(client, OR_QUEUE)


async def test_candidate_with_null_zendesk_status_is_still_unresolved(
    client_and_factory,
):
    """A non-Zendesk row carries a NULL status. A bare `NOT IN` would evaluate to
    NULL and drop it from the OpenReview queue — the explicit `IS NULL` branch is
    what keeps it, exactly as in `get_open_tickets`."""
    client, _ = client_and_factory

    assert "cand-no-zendesk" in await _subjects(client, OR_QUEUE)


# ---------------------------------------------------------------------------
# Envelope, totals, facets
# ---------------------------------------------------------------------------
async def test_openreview_queue_matches_the_main_queue_envelope(client_and_factory):
    """Same shape, so a client reuses the queue's patterns and swaps the URL."""
    client, _ = client_and_factory

    main_body = (await client.get(QUEUE, params={"limit": 200})).json()
    or_body = (await client.get(OR_QUEUE, params={"limit": 200})).json()

    assert set(or_body) == set(main_body) == {"emails", "total", "page_info"}
    assert set(or_body["page_info"]) == set(main_body["page_info"])


async def test_each_queues_total_matches_its_own_rows(client_and_factory):
    """The count and the page must describe the same set — the filter is applied
    in one shared place precisely so they cannot disagree."""
    client, _ = client_and_factory

    for url, expected in ((QUEUE, _EXPECTED_MAIN), (OR_QUEUE, _EXPECTED_OPENREVIEW)):
        body = (await client.get(url, params={"limit": 200})).json()
        assert body["total"] == len(body["emails"]) == len(expected), url


async def test_totals_partition_the_table(client_and_factory):
    client, _ = client_and_factory

    main_total = (await client.get(QUEUE, params={"limit": 1})).json()["total"]
    or_total = (await client.get(OR_QUEUE, params={"limit": 1})).json()["total"]

    assert main_total + or_total == len(_ROWS)


async def test_facets_apply_the_same_exclusion_as_the_list(client_and_factory):
    """The status bar sits beside the main queue, so it must describe the same
    set — otherwise the counts exceed the rows underneath them."""
    client, _ = client_and_factory

    facets = (await client.get(FACETS)).json()
    main_total = (await client.get(QUEUE, params={"limit": 1})).json()["total"]

    assert sum(facets["by_source"].values()) == main_total
    # The unresolved candidates are Zendesk rows with status open/new; if the
    # facets ignored the exclusion these buckets would still count them.
    assert facets["by_zendesk_status"].get("open", 0) == 3  # plain, key-absent, legacy
    assert "new" not in facets["by_zendesk_status"]  # only cand-new had it


# ---------------------------------------------------------------------------
# The new endpoint composes with the existing filters
# ---------------------------------------------------------------------------
async def test_openreview_queue_composes_with_other_filters(client_and_factory):
    client, _ = client_and_factory

    assert await _subjects(client, OR_QUEUE, zendesk_status="open") == ["cand-open"]
    assert await _subjects(client, OR_QUEUE, search="cand-new") == ["cand-new"]
    assert await _subjects(client, OR_QUEUE, lane="faq") == []


async def test_openreview_queue_paginates(client_and_factory):
    client, _ = client_and_factory

    first = (await client.get(OR_QUEUE, params={"limit": 1, "offset": 0})).json()
    second = (await client.get(OR_QUEUE, params={"limit": 1, "offset": 1})).json()

    assert len(first["emails"]) == len(second["emails"]) == 1
    assert first["total"] == second["total"] == len(_EXPECTED_OPENREVIEW)
    assert first["emails"][0]["id"] != second["emails"][0]["id"]


async def test_openreview_queue_enforces_the_same_bounds(client_and_factory):
    client, _ = client_and_factory

    assert (await client.get(OR_QUEUE, params={"limit": 500})).status_code == 422
    assert (await client.get(OR_QUEUE, params={"offset": -1})).status_code == 422


async def test_openreview_queue_rejects_an_inverted_date_range(client_and_factory):
    """Shares `_received_range` with /queue, so it inherits the same 422."""
    client, _ = client_and_factory

    resp = await client.get(
        OR_QUEUE, params={"received_after": "2026-02-01", "received_before": "2026-01-01"}
    )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Nothing else narrowed
# ---------------------------------------------------------------------------
async def test_analytics_style_unfiltered_read_still_sees_every_email(
    client_and_factory,
):
    """`analytics.py` aggregates via `get_email_queue` with no OpenReview mode.

    The default is deliberately "no filter" rather than "exclude", so analytics
    keeps counting candidates. A default-on exclusion would have silently dropped
    them from every chart.
    """
    from app.repositories.email_repository import EmailRepository

    _, factory = client_and_factory
    async with factory() as session:
        rows = await EmailRepository().get_email_queue(session, limit=200, offset=0)

    assert len(rows) == len(_ROWS)
