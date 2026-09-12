"""``openreview_readers`` on the per-email fetch endpoints.

FULLY HERMETIC — NO NETWORK. Both OpenReview seams (``get_openreview_client``,
``openreview_get_note``) are monkeypatched on the endpoint module, so neither
the SDK nor a client is ever constructed.

⚠️ THE CALL-COUNT ASSERTIONS ARE THE POINT OF THIS FILE, not decoration. Every
state-shape assertion here also passes if the endpoint fetches from OpenReview
unconditionally and then throws the result away for non-candidates — the
response body is identical either way. Only the recorder's call log can tell
"never asked" from "asked, ignored the answer", and the difference is a live
third-party round-trip on every email a chair opens. So the not-applicable cases
assert on ``rec.client_calls`` / ``rec.get_calls`` and not merely on ``state``.
"""

import asyncio

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from app.api.v1 import emails as emails_module
from app.db.database import get_db
from app.db.models import Base, Email
from app.integrations.openreview import (
    OpenReviewNote,
    OpenReviewNoteNotFoundError,
    OpenReviewPermissionError,
)
from app.integrations.openreview.client import (
    OpenReviewAuthError,
    OpenReviewCredentialError,
    OpenReviewDependencyError,
)

NOTE_ID = "jnHgRMHgrm"
FORUM_ID = "ll0avn6ylq"
VENUE = "AAAI.org/2027/Conference"
READERS = [
    f"{VENUE}/Program_Chairs",
    f"{VENUE}/Submission1030/Reviewers",
    f"{VENUE}/Submission1030/Authors",
]

DETAIL_URL = "/api/v1/emails/{id}"
TICKET_URL = "/api/v1/emails/by-ticket/{ticket_id}"

#: Every state returns the SAME key set, so a consumer never has to tell an
#: absent key from a null value.
EXPECTED_KEYS = {"state", "readers", "note_id", "error", "error_type"}


def _extraction(**overrides) -> dict:
    base = {
        "submission_numbers": ["1030"],
        "openreview_forum_ids": [FORUM_ID],
        "openreview_note_id": NOTE_ID,
        "openreview_notification_sender": "aaai2027-notifications@openreview.net",
        "extracted_reply_text": "Thank you — I will submit my review by Friday.",
        "authors": [],
        "method": "llm_distiller",
        "openreview_reply_candidate": True,
    }
    base.update(overrides)
    return base


# --- recording seams --------------------------------------------------------


class Recorder:
    """Records every call to the two OpenReview seams a read can reach."""

    def __init__(self, *, note=None, get_exc=None, client_exc=None, delay=0.0):
        self._note = (
            note
            if note is not None
            else OpenReviewNote(
                id=NOTE_ID, forum=FORUM_ID, readers=list(READERS), content={}
            )
        )
        self._get_exc = get_exc
        self._client_exc = client_exc
        self._delay = delay
        self.client_calls = 0
        self.get_calls: list[str] = []

    def client(self, _settings):
        self.client_calls += 1
        if self._client_exc is not None:
            raise self._client_exc
        return object()

    def get_note(self, _client, note_id):
        self.get_calls.append(note_id)
        if self._delay:
            import time

            time.sleep(self._delay)
        if self._get_exc is not None:
            raise self._get_exc
        return self._note

    def install(self, monkeypatch):
        monkeypatch.setattr(emails_module, "get_openreview_client", self.client)
        monkeypatch.setattr(emails_module, "openreview_get_note", self.get_note)
        return self


@pytest.fixture
def rec(monkeypatch):
    return Recorder().install(monkeypatch)


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

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, factory
    main.app.dependency_overrides.clear()
    await engine.dispose()


async def _seed(factory, **overrides) -> str:
    fields = {
        "sender": "reviewer@example.edu",
        "subject": "Re: [AAAI 2027] SPC commented on a paper",
        "body": "Thank you — I will submit my review by Friday.",
        "status": "DRAFT_GENERATED",
        "routing": {"lane": "human_review"},
        "draft": {"draft_text": "some AI draft"},
        "extraction": _extraction(),
    }
    fields.update(overrides)
    async with factory() as session:
        email = Email(**fields)
        session.add(email)
        await session.commit()
        await session.refresh(email)
        return str(email.id)


async def _get(client, email_id):
    resp = await client.get(DETAIL_URL.format(id=email_id))
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# Fetched
# ---------------------------------------------------------------------------
async def test_candidate_with_a_valid_note_returns_the_parent_readers(
    client_and_factory, rec
):
    client, factory = client_and_factory
    email_id = await _seed(factory)

    body = (await _get(client, email_id))["email"]["openreview_readers"]

    assert body["state"] == "fetched"
    assert body["readers"] == READERS
    assert body["note_id"] == NOTE_ID
    assert body["error"] is None
    assert body["error_type"] is None


async def test_it_asks_openreview_for_the_extracted_note_id(client_and_factory, rec):
    client, factory = client_and_factory
    email_id = await _seed(factory)

    await _get(client, email_id)

    assert rec.get_calls == [NOTE_ID]
    assert rec.client_calls == 1


async def test_an_empty_reader_list_is_fetched_not_unavailable(
    client_and_factory, monkeypatch
):
    """``[]`` is a fourth, genuine fact — a note that names no readers.

    It must stay distinguishable from the ``None`` that both non-fetched states
    carry, or "nobody will see this" collapses into "we don't know who will".
    """
    Recorder(
        note=OpenReviewNote(id=NOTE_ID, forum=FORUM_ID, readers=[], content={})
    ).install(monkeypatch)
    client, factory = client_and_factory
    email_id = await _seed(factory)

    body = (await _get(client, email_id))["email"]["openreview_readers"]

    assert body["state"] == "fetched"
    assert body["readers"] == []
    assert body["readers"] is not None


# ---------------------------------------------------------------------------
# Failed — and the rest of the email still renders
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "exc, expected_type",
    [
        (OpenReviewNoteNotFoundError("gone"), "OpenReviewNoteNotFoundError"),
        (OpenReviewPermissionError("nope"), "OpenReviewPermissionError"),
        (RuntimeError("something nobody anticipated"), "RuntimeError"),
    ],
)
async def test_a_failed_fetch_is_reported_as_failed_not_as_absent(
    client_and_factory, monkeypatch, exc, expected_type
):
    Recorder(get_exc=exc).install(monkeypatch)
    client, factory = client_and_factory
    email_id = await _seed(factory)

    body = (await _get(client, email_id))["email"]["openreview_readers"]

    assert body["state"] == "failed"
    assert body["readers"] is None
    assert body["note_id"] == NOTE_ID
    assert body["error_type"] == expected_type
    assert body["error"]


@pytest.mark.parametrize(
    "exc, expected_type",
    [
        (OpenReviewCredentialError("no username"), "OpenReviewCredentialError"),
        (OpenReviewDependencyError("not installed"), "OpenReviewDependencyError"),
        (OpenReviewAuthError("rejected"), "OpenReviewAuthError"),
    ],
)
async def test_a_failure_building_the_client_is_failed_too(
    client_and_factory, monkeypatch, exc, expected_type
):
    """A deployment with no OpenReview credentials must not 500 the detail page.

    The failure is upstream of ``get_note``, so it would escape a handler that
    only guarded the fetch itself.
    """
    r = Recorder(client_exc=exc).install(monkeypatch)
    client, factory = client_and_factory
    email_id = await _seed(factory)

    body = (await _get(client, email_id))["email"]["openreview_readers"]

    assert body["state"] == "failed"
    assert body["error_type"] == expected_type
    assert r.get_calls == []


async def test_the_whole_email_still_returns_when_openreview_is_down(
    client_and_factory, monkeypatch
):
    """The reason failure is reported in-band rather than raised."""
    Recorder(get_exc=OpenReviewNoteNotFoundError("gone")).install(monkeypatch)
    client, factory = client_and_factory
    email_id = await _seed(factory)

    payload = await _get(client, email_id)

    assert payload["email"]["id"] == int(email_id)
    assert payload["email"]["subject"] == "Re: [AAAI 2027] SPC commented on a paper"
    assert payload["email"]["extraction"]["openreview_note_id"] == NOTE_ID
    assert payload["email"]["draft"] == {"draft_text": "some AI draft"}
    assert "audit_trail" in payload
    assert payload["email"]["openreview_readers"]["state"] == "failed"


async def test_a_hanging_openreview_does_not_hang_the_detail_page(
    client_and_factory, monkeypatch
):
    monkeypatch.setattr(emails_module, "_OPENREVIEW_READERS_TIMEOUT_SECONDS", 0.05)
    Recorder(delay=1.0).install(monkeypatch)
    client, factory = client_and_factory
    email_id = await _seed(factory)

    body = (await _get(client, email_id))["email"]["openreview_readers"]

    assert body["state"] == "failed"
    assert body["error_type"] == "TimeoutError"
    assert body["readers"] is None


# ---------------------------------------------------------------------------
# Not applicable — and NO OpenReview call is attempted
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "extraction, why",
    [
        (_extraction(openreview_reply_candidate=False), "not a candidate"),
        (None, "never examined (extraction is NULL)"),
        ({}, "empty extraction record"),
        (_extraction(openreview_note_id=None), "candidate flag but no note id"),
        (_extraction(openreview_note_id="   "), "blank note id"),
    ],
)
async def test_a_non_candidate_is_not_applicable_and_costs_no_openreview_call(
    client_and_factory, rec, extraction, why
):
    client, factory = client_and_factory
    email_id = await _seed(factory, extraction=extraction)

    body = (await _get(client, email_id))["email"]["openreview_readers"]

    assert body["state"] == "not_applicable", why
    assert body["readers"] is None
    assert body["note_id"] is None
    assert body["error"] is None
    assert body["error_type"] is None
    # The assertion that actually distinguishes "never asked" from "asked and
    # discarded" — the response body above is identical in both cases.
    assert rec.client_calls == 0, f"built an OpenReview client for {why}"
    assert rec.get_calls == [], f"called OpenReview for {why}"


async def test_the_queue_listing_never_triggers_a_lookup(client_and_factory, rec):
    """Guards the N+1: this field is on the DETAIL serializer only.

    ``/queue/openreview`` is a page of nothing BUT candidates, so putting the
    lookup in ``_email_to_dict`` would mean up to ``limit`` live OpenReview
    round-trips per list render.
    """
    client, factory = client_and_factory
    await _seed(factory)
    await _seed(factory)

    resp = await client.get("/api/v1/emails/queue/openreview")

    assert resp.status_code == 200
    assert len(resp.json()["emails"]) == 2
    assert "openreview_readers" not in resp.json()["emails"][0]
    assert rec.client_calls == 0
    assert rec.get_calls == []


# ---------------------------------------------------------------------------
# Shape + freshness
# ---------------------------------------------------------------------------
async def test_every_state_carries_the_same_keys(client_and_factory, monkeypatch):
    client, factory = client_and_factory
    fetched_id = await _seed(factory)
    plain_id = await _seed(factory, extraction=None)

    Recorder().install(monkeypatch)
    fetched = (await _get(client, fetched_id))["email"]["openreview_readers"]
    not_applicable = (await _get(client, plain_id))["email"]["openreview_readers"]

    Recorder(get_exc=OpenReviewNoteNotFoundError("gone")).install(monkeypatch)
    failed = (await _get(client, fetched_id))["email"]["openreview_readers"]

    for body in (fetched, not_applicable, failed):
        assert set(body) == EXPECTED_KEYS
    assert {fetched["state"], not_applicable["state"], failed["state"]} == {
        "fetched",
        "not_applicable",
        "failed",
    }


async def test_readers_are_re_read_on_every_request_never_cached(
    client_and_factory, monkeypatch
):
    """The staleness property the live-fetch decision buys.

    Readers are a live OpenReview fact and can change after detection — which is
    exactly why they are not stored. A second read must show the new audience,
    and must not be served from anything remembered by the first.
    """
    first = Recorder().install(monkeypatch)
    client, factory = client_and_factory
    email_id = await _seed(factory)

    before = (await _get(client, email_id))["email"]["openreview_readers"]
    assert before["readers"] == READERS

    widened = [*READERS, f"{VENUE}/Submission1030/Area_Chairs"]
    second = Recorder(
        note=OpenReviewNote(id=NOTE_ID, forum=FORUM_ID, readers=widened, content={})
    ).install(monkeypatch)
    after = (await _get(client, email_id))["email"]["openreview_readers"]

    assert after["readers"] == widened
    assert first.get_calls == [NOTE_ID]
    assert second.get_calls == [NOTE_ID]


async def test_the_by_ticket_route_serves_the_same_field(client_and_factory, rec):
    """``/by-ticket`` documents its shape as identical to ``/{email_id}``.

    Both go through ``_email_detail_dict``, so a chair reaching a candidate via a
    ticket deep link sees the audience too — and the documented parity holds.
    """
    client, factory = client_and_factory
    await _seed(factory, zendesk_ticket_id=21567, source="zendesk")

    resp = await client.get(TICKET_URL.format(ticket_id=21567))

    assert resp.status_code == 200
    body = resp.json()["email"]["openreview_readers"]
    assert body["state"] == "fetched"
    assert body["readers"] == READERS


async def test_the_lookup_does_not_block_the_event_loop(client_and_factory, monkeypatch):
    """The SDK is synchronous, so it must run off the loop.

    Asserted by keeping a coroutine ticking while the fetch sleeps: if the call
    ran inline, the loop would be frozen for its whole duration and the ticker
    could not advance.
    """
    Recorder(delay=0.3).install(monkeypatch)
    client, factory = client_and_factory
    email_id = await _seed(factory)

    ticks = 0

    async def _ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(_ticker())
    try:
        body = (await _get(client, email_id))["email"]["openreview_readers"]
    finally:
        task.cancel()

    assert body["state"] == "fetched"
    assert ticks > 5, f"event loop appears to have been blocked (ticks={ticks})"
