"""``received_at`` records when the REQUESTER wrote in, not when we inserted the row.

The column only carries ``server_default=func.now()``, so every ingestion path
that knows the real instant has to pass it explicitly or the row silently records
poll/insert time. There are FOUR such paths and each is covered here:

1. Zendesk full-pipeline branch  (initial inquiry present, non-closed)
2. Zendesk closed/archived branch (``create_email`` direct, pipeline skipped)
3. Zendesk bare-pending branch    (``create_email`` direct, no inquiry found)
4. Manual ``POST /api/v1/emails/ingest``

Paths 1 and 4 flow through ``orchestrator._compute`` (which reads
``email_data["timestamp"]``); paths 2 and 3 build their dict inline in the
adapter. Both mechanisms are exercised end-to-end against a real row read back
from the DB, not by asserting on an intermediate dict.

The discriminating assertion in every case is that ``received_at`` equals the
SOURCE timestamp, which is deliberately years away from ``now`` — a test that
only checked "not None" would pass against the pre-fix code, since
``server_default`` always produced a non-null value.

SCOPE LIMIT — this suite runs on SQLite, whose ``DATETIME`` stores the wall-clock
fields and DISCARDS ``tzinfo`` (Postgres ``timestamptz`` round-trips offset-aware).
Values read back are therefore naive and are re-stamped UTC via ``_as_utc`` before
comparison. This is also why the pipeline normalizes to UTC before writing: an
offset-bearing timestamp would otherwise persist as its local wall clock. See
``test_ingest_non_utc_offset_is_normalized_to_utc``.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from app.db.database import Base, get_db
from app.integrations.zendesk.adapter import SyncResult, ZendeskIngestAdapter
from app.models.enums import EmailStatus
from app.pipeline.orchestrator import EmailPipeline, _parse_received_at
from app.repositories.email_repository import EmailRepository

# The one source timestamp under test. Far from "now" on purpose: that gap is
# what makes "used the real timestamp" distinguishable from "used insert time".
TICKET_CREATED_ISO = "2026-03-04T11:15:00Z"
TICKET_CREATED_UTC = datetime(2026, 3, 4, 11, 15, tzinfo=timezone.utc)

REQUESTER = {"email": "author@uni.edu", "name": "A Author"}
REQUESTER_ID = 99

INGEST_BODY = {
    "from": "author@uni.edu",
    "to": "chairs@conf.org",
    "subject": "Deadline question",
    "body": "When is the paper submission deadline this year?",
}


def _as_utc(value: datetime) -> datetime:
    """Re-stamp a SQLite-naive datetime as UTC so instants are comparable."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _assert_is_source_time(received_at: datetime) -> None:
    """The row carries the SOURCE timestamp, not this run's insert time."""
    assert received_at is not None
    assert _as_utc(received_at) == TICKET_CREATED_UTC
    # Stated separately from the equality above so the intent survives someone
    # later changing the constant: whatever the source time is, it must not be
    # the moment the row was written.
    assert abs(_as_utc(received_at) - datetime.now(timezone.utc)) > timedelta(days=1)


def _assert_is_insert_time(received_at: datetime) -> None:
    """No usable source timestamp -> the column's server_default, never NULL."""
    assert received_at is not None
    assert abs(_as_utc(received_at) - datetime.now(timezone.utc)) < timedelta(minutes=5)


# --- fixtures ---------------------------------------------------------------


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
        async with factory() as s:
            yield s

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, factory
    main.app.dependency_overrides.clear()
    await engine.dispose()


# --- test data builders ----------------------------------------------------


def _ticket(ticket_id: int, status: str, **overrides) -> dict:
    ticket = {
        "id": ticket_id,
        "status": status,
        "subject": "Paper withdrawn by mistake",
        "description": "My paper was withdrawn by mistake.",
        "requester_id": REQUESTER_ID,
        "created_at": TICKET_CREATED_ISO,
        "updated_at": "2026-03-09T08:00:00Z",
    }
    ticket.update(overrides)
    return ticket


def _inquiry_messages() -> list[dict]:
    """A public message FROM the requester -> ``_find_initial_inquiry`` matches."""
    return [
        {
            "zendesk_comment_id": 1,
            "public": True,
            "author_id": REQUESTER_ID,
            "author_role": "end-user",
            "plain_body": "My paper was withdrawn by mistake.",
            "html_body": None,
            "created_at": TICKET_CREATED_UTC,
            "via_channel": "email",
        }
    ]


def _no_inquiry_messages() -> list[dict]:
    """Only a private agent note -> no initial inquiry -> bare-pending branch."""
    return [
        {
            "zendesk_comment_id": 2,
            "public": False,
            "author_id": 5,
            "author_role": "agent",
            "plain_body": "Internal triage note.",
            "html_body": None,
            "created_at": TICKET_CREATED_UTC + timedelta(minutes=5),
            "via_channel": "web",
        }
    ]


# --- path 1: Zendesk full-pipeline branch ----------------------------------


async def test_full_pipeline_branch_uses_ticket_created_at(session):
    """An inquiry-bearing ticket is drafted AND stamped with the ticket's own time.

    This branch hands ``created_at`` to the pipeline as
    ``email_data["timestamp"]``, so it proves the ``_compute`` record wiring, not
    just the adapter's inline dicts.
    """
    result = SyncResult()
    email_id = await ZendeskIngestAdapter()._ingest_new_ticket(
        session, _ticket(5001, "open"), REQUESTER, _inquiry_messages(), result
    )

    row = await EmailRepository().get_email_by_id(session, email_id)
    _assert_is_source_time(row.received_at)
    # The pipeline really ran (otherwise this would be a vacuous pass on a
    # branch that skipped drafting).
    assert result.classified == 1
    assert row.status == EmailStatus.DRAFT_GENERATED.value


# --- path 2: Zendesk closed/archived branch --------------------------------


async def test_closed_branch_uses_ticket_created_at(session):
    """A closed ticket skips the pipeline but is still stamped with its real time."""
    result = SyncResult()
    email_id = await ZendeskIngestAdapter()._ingest_new_ticket(
        session, _ticket(5002, "closed"), REQUESTER, _inquiry_messages(), result
    )

    row = await EmailRepository().get_email_by_id(session, email_id)
    _assert_is_source_time(row.received_at)
    # This branch is ingest-for-visibility only: no classification, no draft.
    assert result.closed_ingested == 1
    assert result.classified == 0


# --- path 3: Zendesk bare-pending branch -----------------------------------


async def test_bare_pending_branch_uses_ticket_created_at(session):
    """A ticket with no identifiable inquiry is still stamped with its real time."""
    result = SyncResult()
    email_id = await ZendeskIngestAdapter()._ingest_new_ticket(
        session, _ticket(5003, "open"), REQUESTER, _no_inquiry_messages(), result
    )

    row = await EmailRepository().get_email_by_id(session, email_id)
    _assert_is_source_time(row.received_at)
    assert result.classified == 0
    assert result.closed_ingested == 0


# --- the three Zendesk branches must not change status semantics -----------


@pytest.mark.parametrize(
    "ticket_status,messages,expected_status",
    [
        ("open", _inquiry_messages(), EmailStatus.DRAFT_GENERATED.value),
        ("closed", _inquiry_messages(), EmailStatus.ARCHIVED.value),
        ("open", _no_inquiry_messages(), EmailStatus.PENDING.value),
    ],
    ids=["full_pipeline", "closed_archived", "bare_pending"],
)
async def test_received_at_wiring_does_not_touch_status_fields(
    session, ticket_status, messages, expected_status
):
    """Stamping received_at is a non-event for status / zendesk_status.

    ``zendesk_status`` is written later by ``apply_zendesk_fields`` in
    ``_process_ticket``, so at this point it is still None on every branch —
    pinned so a future edit can't start setting it here as a side effect.
    """
    email_id = await ZendeskIngestAdapter()._ingest_new_ticket(
        session, _ticket(5100, ticket_status), REQUESTER, messages, SyncResult()
    )

    row = await EmailRepository().get_email_by_id(session, email_id)
    assert row.status == expected_status
    assert row.zendesk_status is None


# --- Zendesk fallback behaviour --------------------------------------------


@pytest.mark.parametrize(
    "ticket_status,messages",
    [
        ("open", _inquiry_messages()),
        ("closed", _inquiry_messages()),
        ("open", _no_inquiry_messages()),
    ],
    ids=["full_pipeline", "closed_archived", "bare_pending"],
)
@pytest.mark.parametrize("created_at", [None, ""], ids=["absent", "empty"])
async def test_missing_ticket_created_at_falls_back_to_insert_time(
    session, ticket_status, messages, created_at
):
    """No usable ticket time -> server_default fires; the column is never NULL.

    Note the row dicts OMIT the key rather than pass None. That is defensive, not
    load-bearing: SQLAlchemy 2.0 omits a None-valued attribute on a
    server_default column from the INSERT, so this assertion holds either way.
    ``test_compute_record_omits_received_at_unless_parseable`` is what pins the
    absent-vs-None distinction.
    """
    ticket = _ticket(5200, ticket_status)
    if created_at is None:
        del ticket["created_at"]
    else:
        ticket["created_at"] = created_at

    email_id = await ZendeskIngestAdapter()._ingest_new_ticket(
        session, ticket, REQUESTER, messages, SyncResult()
    )

    row = await EmailRepository().get_email_by_id(session, email_id)
    _assert_is_insert_time(row.received_at)


async def test_malformed_ticket_created_at_raises_for_the_direct_branches(session):
    """A malformed ticket ``created_at`` raises rather than silently defaulting.

    Pins a REAL asymmetry between the two mechanisms, so neither side drifts
    unnoticed: the adapter reuses ``_parse_dt`` (which raises, as it already did
    for ``zendesk_created_at``) while the pipeline's ``_parse_received_at`` logs
    and degrades. Raising here is safe because ``sync`` wraps every ticket in a
    per-ticket try/except that counts the failure and moves on, and it happens
    BEFORE ``create_email`` so no half-decorated row is left behind.
    """
    with pytest.raises(ValueError):
        await ZendeskIngestAdapter()._ingest_new_ticket(
            session,
            _ticket(5300, "closed", created_at="not-a-date"),
            REQUESTER,
            _inquiry_messages(),
            SyncResult(),
        )


# --- path 4: manual POST /ingest ------------------------------------------


async def test_ingest_endpoint_uses_request_timestamp(client):
    """The manual entry point honours the caller's ``timestamp`` field."""
    c, factory = client
    response = await c.post(
        "/api/v1/emails/ingest", json={**INGEST_BODY, "timestamp": TICKET_CREATED_ISO}
    )
    assert response.status_code == 200, response.text

    async with factory() as s:
        row = await EmailRepository().get_email_by_id(s, response.json()["email_id"])
    _assert_is_source_time(row.received_at)


@pytest.mark.parametrize(
    "payload_extra",
    [{}, {"timestamp": ""}, {"timestamp": "yesterday-ish"}],
    ids=["omitted", "empty_string", "malformed"],
)
async def test_ingest_endpoint_falls_back_to_insert_time(client, payload_extra):
    """Absent/empty/malformed ``timestamp`` -> insert time, and still HTTP 200.

    Pins the CURRENT contract deliberately: a malformed value is logged and
    ignored rather than rejected with 422. If that is ever tightened, this test
    is the one that should fail and be updated.
    """
    c, factory = client
    response = await c.post(
        "/api/v1/emails/ingest", json={**INGEST_BODY, **payload_extra}
    )
    assert response.status_code == 200, response.text

    async with factory() as s:
        row = await EmailRepository().get_email_by_id(s, response.json()["email_id"])
    _assert_is_insert_time(row.received_at)


async def test_ingest_non_utc_offset_is_normalized_to_utc(client):
    """An offset-bearing timestamp persists as the correct UTC INSTANT.

    Regression guard for a real defect: SQLite's DATETIME keeps the wall-clock
    fields and drops tzinfo, so without normalizing first, 14:45-05:00 persisted
    as 14:45 instead of 19:45 — silently wrong by five hours, on SQLite only.
    """
    c, factory = client
    response = await c.post(
        "/api/v1/emails/ingest",
        json={**INGEST_BODY, "timestamp": "2026-02-09T14:45:00-05:00"},
    )
    assert response.status_code == 200, response.text

    async with factory() as s:
        row = await EmailRepository().get_email_by_id(s, response.json()["email_id"])
    assert _as_utc(row.received_at) == datetime(2026, 2, 9, 19, 45, tzinfo=timezone.utc)


# --- reprocess must NOT disturb an existing row's received_at --------------


async def test_reprocess_email_leaves_received_at_untouched(session):
    """A manual re-draft refreshes outputs only; the original arrival time stays.

    ``reprocess_email`` builds ``email_data`` with no "timestamp" key at all, so
    nothing is added to the record — and ``update_email_outputs`` allowlists the
    columns it writes, so received_at could not leak through even if it were.
    """
    pipeline = EmailPipeline()
    email_id = await ZendeskIngestAdapter()._ingest_new_ticket(
        session, _ticket(5400, "open"), REQUESTER, _inquiry_messages(), SyncResult()
    )
    repo = EmailRepository()
    before = (await repo.get_email_by_id(session, email_id)).received_at
    _assert_is_source_time(before)

    await pipeline.reprocess_email(
        session, await repo.get_email_by_id(session, email_id)
    )

    after = (await repo.get_email_by_id(session, email_id)).received_at
    assert after == before


async def test_reprocess_with_thread_leaves_received_at_untouched(session):
    """A requester follow-up re-drafts over the thread but never re-stamps arrival.

    Notably the follow-up message carries its OWN, later ``created_at``; the row
    must keep the ORIGINAL inquiry time regardless.
    """
    pipeline = EmailPipeline()
    email_id = await ZendeskIngestAdapter()._ingest_new_ticket(
        session, _ticket(5401, "open"), REQUESTER, _inquiry_messages(), SyncResult()
    )
    repo = EmailRepository()
    before = (await repo.get_email_by_id(session, email_id)).received_at
    _assert_is_source_time(before)

    await pipeline.reprocess_email_with_thread(
        session,
        await repo.get_email_by_id(session, email_id),
        [
            *_inquiry_messages(),
            {
                "zendesk_comment_id": 7,
                "public": True,
                "author_id": REQUESTER_ID,
                "author_role": "end-user",
                "plain_body": "Following up on my withdrawal request.",
                "html_body": None,
                "created_at": TICKET_CREATED_UTC + timedelta(days=30),
                "via_channel": "email",
            },
        ],
    )

    after = (await repo.get_email_by_id(session, email_id)).received_at
    assert after == before


# --- the record dict omits the key rather than passing None ---------------


@pytest.mark.parametrize(
    "email_data_extra,expected_present",
    [
        ({"timestamp": TICKET_CREATED_ISO}, True),
        ({"timestamp": ""}, False),
        ({}, False),
        ({"timestamp": "not-a-date"}, False),
    ],
    ids=["parseable", "empty", "key_absent", "malformed"],
)
async def test_compute_record_omits_received_at_unless_parseable(
    session, email_data_extra, expected_present
):
    """``record`` carries received_at ONLY for a real instant — absent, never None.

    Asserted on the record dict (not a persisted row) because "absent vs None" is
    invisible downstream: both would read back as a non-null column, one via the
    server default and one via an IntegrityError.
    """
    computed = await EmailPipeline()._compute(
        {
            "from": INGEST_BODY["from"],
            "subject": INGEST_BODY["subject"],
            "body": INGEST_BODY["body"],
            **email_data_extra,
        },
        session,
    )
    assert ("received_at" in computed.record) is expected_present
    if expected_present:
        assert computed.record["received_at"] == TICKET_CREATED_UTC


# --- parser unit matrix ---------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-03-04T11:15:00Z", TICKET_CREATED_UTC),
        ("2026-03-04T11:15:00+00:00", TICKET_CREATED_UTC),
        # Naive is assumed UTC (never system-local, which would make the stored
        # instant depend on the server's timezone).
        ("2026-03-04T11:15:00", TICKET_CREATED_UTC),
        # Offset-bearing input is CONVERTED, not merely carried.
        ("2026-03-04T06:15:00-05:00", TICKET_CREATED_UTC),
        (TICKET_CREATED_UTC, TICKET_CREATED_UTC),
        (datetime(2026, 3, 4, 11, 15), TICKET_CREATED_UTC),
        # Everything below means "leave received_at alone".
        ("", None),
        ("   ", None),
        (None, None),
        ("not-a-date", None),
        ("04/03/2026", None),
        # An epoch int is deliberately NOT interpreted: silently reading a number
        # as a Unix timestamp is the kind of guess that produces 1970 rows.
        (1772622900, None),
        (0, None),
    ],
)
def test_parse_received_at_matrix(value, expected):
    assert _parse_received_at(value) == expected
