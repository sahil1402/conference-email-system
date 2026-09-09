"""POST /emails/{id}/post-openreview-reply — relaying a reply onward.

FULLY HERMETIC — NO NETWORK. The three OpenReview seams
(``get_openreview_client``, ``openreview_get_note``,
``openreview_post_comment_reply``) are monkeypatched on the endpoint module, so
neither the SDK nor a client is ever constructed. Verified by running this file
with every network syscall blocked.

Asserted through the REAL endpoint via ASGITransport rather than by calling the
handler, because the ordering this endpoint depends on — gate, then fetch, then
post — is only meaningful as an HTTP request/response.
"""

import asyncio
import time

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from app.api.v1 import emails as emails_module
from app.core.config import settings
from app.db.database import get_db
from app.db.models import AuditLog, Base, Email
from app.integrations.openreview import (
    OpenReviewNote,
    OpenReviewNoteNotFoundError,
    OpenReviewPermissionError,
    OpenReviewThreadMismatchError,
    PostedComment,
)

NOTE_ID = "jnHgRMHgrm"
FORUM_ID = "ll0avn6ylq"
VENUE = "AAAI.org/2027/Conference"
PARENT_READERS = [f"{VENUE}/Program_Chairs", f"{VENUE}/Submission1030/Reviewers"]
ORIGINAL_TEXT = "I will provide review before the deadline."
EDITED_TEXT = "Thank you — I will submit my review by Friday."

URL = "/api/v1/emails/{id}/post-openreview-reply"


def _extraction(**overrides) -> dict:
    """A candidate extraction, exactly as the pipeline stores it."""
    base = {
        "submission_numbers": ["1030"],
        "openreview_forum_ids": [FORUM_ID],
        "openreview_note_id": NOTE_ID,
        "openreview_notification_sender": "aaai2027-notifications@openreview.net",
        "extracted_reply_text": ORIGINAL_TEXT,
        "authors": [],
        "method": "llm_distiller",
        # Stored by the pipeline as a computed field; a plain key here.
        "openreview_reply_candidate": True,
    }
    base.update(overrides)
    return base


# --- recording seams --------------------------------------------------------


class Recorder:
    """Records every call to the three OpenReview seams."""

    def __init__(
        self,
        *,
        note=None,
        get_exc=None,
        post_exc=None,
        posted=None,
        client_delay=0.0,
    ):
        self._note = note if note is not None else OpenReviewNote(
            id=NOTE_ID, forum=FORUM_ID, readers=list(PARENT_READERS), content={}
        )
        self._get_exc = get_exc
        self._post_exc = post_exc
        self._posted = posted or PostedComment(edit_id="edit-1", note_id="new-1")
        #: Stands in for the network LOGIN that the real client's constructor
        #: performs. Blocking, on purpose — that is the whole property under
        #: test.
        self._client_delay = client_delay
        self.client_calls = 0
        self.get_calls = []
        self.post_calls = []

    def client(self, _settings):
        self.client_calls += 1
        if self._client_delay:
            time.sleep(self._client_delay)
        return object()

    def get_note(self, client, note_id):
        self.get_calls.append(note_id)
        if self._get_exc is not None:
            raise self._get_exc
        return self._note

    def post(self, client, **kwargs):
        self.post_calls.append(kwargs)
        if self._post_exc is not None:
            raise self._post_exc
        return self._posted


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(emails_module, "get_openreview_client", r.client)
    monkeypatch.setattr(emails_module, "openreview_get_note", r.get_note)
    monkeypatch.setattr(emails_module, "openreview_post_comment_reply", r.post)
    monkeypatch.setattr(settings, "OPENREVIEW_VENUE_ID", VENUE)
    return r


def use(monkeypatch, recorder):
    """Swap in a differently-configured Recorder mid-test."""
    monkeypatch.setattr(emails_module, "get_openreview_client", recorder.client)
    monkeypatch.setattr(emails_module, "openreview_get_note", recorder.get_note)
    monkeypatch.setattr(emails_module, "openreview_post_comment_reply", recorder.post)
    return recorder


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
        "body": ORIGINAL_TEXT,
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


async def _audit_actions(factory, email_id: str) -> list[str]:
    async with factory() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.email_id == int(email_id))
            )
        ).scalars().all()
        return [r.action for r in rows]


async def _post(client, email_id, **overrides):
    body = {
        "reply_text": EDITED_TEXT,
        "submission_number": 1030,
        "posted_by": "chair1",
    }
    body.update(overrides)
    return await client.post(URL.format(id=email_id), json=body)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
async def test_posts_the_reply_and_returns_the_identifiers(client_and_factory, rec):
    client, factory = client_and_factory
    email_id = await _seed(factory)

    resp = await _post(client, email_id)

    assert resp.status_code == 200
    posted = resp.json()["openreview_post"]
    assert posted["state"] == "posted"
    assert posted["note_id"] == "new-1"
    assert posted["parent_note_id"] == NOTE_ID
    assert posted["forum_id"] == FORUM_ID
    assert posted["venue_id"] == VENUE


async def test_posts_with_the_parent_note_as_replyto_and_its_readers(
    client_and_factory, rec
):
    client, factory = client_and_factory
    email_id = await _seed(factory)

    await _post(client, email_id)

    (call,) = rec.post_calls
    assert call["parent_note_id"] == NOTE_ID
    assert call["forum_id"] == FORUM_ID
    assert call["venue_id"] == VENUE
    assert call["submission_number"] == 1030


async def test_audit_records_the_post_with_its_identifiers(client_and_factory, rec):
    client, factory = client_and_factory
    email_id = await _seed(factory)

    await _post(client, email_id)

    actions = await _audit_actions(factory, email_id)
    assert "openreview_post_authorized" in actions
    assert "openreview_comment_posted" in actions

    async with factory() as session:
        row = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "openreview_comment_posted")
            )
        ).scalars().one()
    assert row.actor == "chair1"
    assert row.extra_metadata["note_id"] == "new-1"
    assert row.extra_metadata["forum_id"] == FORUM_ID


async def test_the_post_is_recorded_on_the_email_row(client_and_factory, rec):
    client, factory = client_and_factory
    email_id = await _seed(factory)

    await _post(client, email_id)

    async with factory() as session:
        email = (
            await session.execute(select(Email).where(Email.id == int(email_id)))
        ).scalars().one()
    assert email.draft["openreview_post"]["state"] == "posted"
    # The existing draft text survives — this write must not clobber it.
    assert email.draft["draft_text"] == "some AI draft"


async def test_the_emails_workflow_status_is_not_changed(client_and_factory, rec):
    """Posting to OpenReview is not an email send; the ticket's fate is the next
    piece's decision, not this one's."""
    client, factory = client_and_factory
    email_id = await _seed(factory, status="DRAFT_GENERATED")

    await _post(client, email_id)

    async with factory() as session:
        email = (
            await session.execute(select(Email).where(Email.id == int(email_id)))
        ).scalars().one()
    assert email.status == "DRAFT_GENERATED"


# ---------------------------------------------------------------------------
# The OpenReview SDK is synchronous, so it must not run on the event loop
# ---------------------------------------------------------------------------
async def test_the_openreview_calls_do_not_block_the_event_loop(
    client_and_factory, rec, monkeypatch
):
    """Building the client is a network LOGIN, not a local construction.

    ⚠️ THE DELAY IS ON ``client``, NOT ON ``get_note``, and that placement is
    the entire test. The fetch and the post have run in worker threads since
    commit 11, so a delay on either would keep the loop responsive even with
    the client built inline — the test would pass against the very bug it
    exists to catch. Only a delay in the constructor distinguishes the two
    shapes.

    Asserted by keeping a coroutine ticking for the duration: if the login ran
    on the loop, nothing else in this worker could advance while it was in
    flight, and the ticker would stall.
    """
    use(monkeypatch, Recorder(client_delay=0.3))
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
        resp = await _post(client, email_id)
    finally:
        task.cancel()

    assert resp.status_code == 200
    assert ticks > 5, f"event loop appears to have been blocked (ticks={ticks})"


async def test_one_login_per_relay_not_one_per_call(client_and_factory, rec):
    """The connect and the fetch share a worker thread so they share a CLIENT.

    Giving each its own thread would be equally non-blocking and would silently
    double the logins — the post in step 3 reuses this client, and a second
    round-trip per relay is a real cost with no benefit.
    """
    client, factory = client_and_factory
    email_id = await _seed(factory)

    await _post(client, email_id)

    assert rec.client_calls == 1
    assert len(rec.get_calls) == 1
    assert len(rec.post_calls) == 1


# ---------------------------------------------------------------------------
# The edited text is what gets posted
# ---------------------------------------------------------------------------
async def test_the_chairs_edited_text_is_posted_not_the_extracted_text(
    client_and_factory, rec
):
    """THE substitution guard. The chair reads and edits the text in the review
    UI; posting the originally-extracted string instead would publish something
    they never approved."""
    client, factory = client_and_factory
    email_id = await _seed(factory)

    await _post(client, email_id, reply_text=EDITED_TEXT)

    (call,) = rec.post_calls
    assert call["comment_text"] == EDITED_TEXT
    assert call["comment_text"] != ORIGINAL_TEXT


async def test_an_empty_reply_text_is_rejected_by_validation(client_and_factory, rec):
    client, factory = client_and_factory
    email_id = await _seed(factory)

    resp = await _post(client, email_id, reply_text="")

    assert resp.status_code == 422
    assert rec.post_calls == []


# ---------------------------------------------------------------------------
# Readers come from the parent note
# ---------------------------------------------------------------------------
async def test_readers_are_the_parent_notes_readers(client_and_factory, rec):
    client, factory = client_and_factory
    email_id = await _seed(factory)

    await _post(client, email_id)

    (call,) = rec.post_calls
    assert call["readers"] == PARENT_READERS


async def test_readers_are_not_recomputed_from_the_email(client_and_factory, monkeypatch):
    """The parent's readers are deliberately unusual here, so anything derived
    from the email or the venue instead would not match."""
    client, factory = client_and_factory
    rec = use(
        monkeypatch,
        Recorder(
            note=OpenReviewNote(
                id=NOTE_ID, forum=FORUM_ID, readers=["odd/group/one"], content={}
            )
        ),
    )
    monkeypatch.setattr(settings, "OPENREVIEW_VENUE_ID", VENUE)
    email_id = await _seed(factory)

    await _post(client, email_id)

    assert rec.post_calls[0]["readers"] == ["odd/group/one"]


# ---------------------------------------------------------------------------
# The gate refuses before any OpenReview call
# ---------------------------------------------------------------------------
async def test_non_candidate_is_rejected_without_touching_openreview(
    client_and_factory, rec
):
    """This is not a general 'post anything' endpoint."""
    client, factory = client_and_factory
    email_id = await _seed(
        factory, extraction=_extraction(openreview_reply_candidate=False)
    )

    resp = await _post(client, email_id)

    assert resp.status_code == 409
    assert "not an OpenReview reply candidate" in resp.json()["detail"]["reason"]
    assert rec.client_calls == 0
    assert rec.get_calls == []
    assert rec.post_calls == []


async def test_missing_note_id_is_rejected_without_touching_openreview(
    client_and_factory, rec
):
    client, factory = client_and_factory
    email_id = await _seed(factory, extraction=_extraction(openreview_note_id=None))

    resp = await _post(client, email_id)

    assert resp.status_code == 409
    assert "note id" in resp.json()["detail"]["reason"]
    assert rec.get_calls == []


async def test_missing_forum_id_is_rejected_without_touching_openreview(
    client_and_factory, rec
):
    client, factory = client_and_factory
    email_id = await _seed(factory, extraction=_extraction(openreview_forum_ids=[]))

    resp = await _post(client, email_id)

    assert resp.status_code == 409
    assert "forum id" in resp.json()["detail"]["reason"]
    assert rec.get_calls == []


async def test_null_extraction_is_rejected(client_and_factory, rec):
    client, factory = client_and_factory
    email_id = await _seed(factory, extraction=None)

    resp = await _post(client, email_id)

    assert resp.status_code == 409
    assert rec.get_calls == []


async def test_a_blocked_attempt_is_audited(client_and_factory, rec):
    client, factory = client_and_factory
    email_id = await _seed(
        factory, extraction=_extraction(openreview_reply_candidate=False)
    )

    await _post(client, email_id)

    assert "openreview_post_blocked" in await _audit_actions(factory, email_id)


async def test_posting_twice_is_refused(client_and_factory, rec):
    """Idempotency. A duplicate public comment cannot be withdrawn from here, and
    a double click is the ordinary way it would happen."""
    client, factory = client_and_factory
    email_id = await _seed(factory)

    first = await _post(client, email_id)
    second = await _post(client, email_id)

    assert first.status_code == 200
    assert second.status_code == 409
    assert "already been posted" in second.json()["detail"]["reason"]
    assert len(rec.post_calls) == 1


async def test_unknown_email_is_404(client_and_factory, rec):
    client, _ = client_and_factory

    resp = await _post(client, "999999")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# OpenReview failures surface specifically, and nothing is recorded
# ---------------------------------------------------------------------------
async def test_note_not_found_surfaces_and_nothing_is_posted(
    client_and_factory, monkeypatch
):
    client, factory = client_and_factory
    rec = use(monkeypatch, Recorder(get_exc=OpenReviewNoteNotFoundError("gone")))
    monkeypatch.setattr(settings, "OPENREVIEW_VENUE_ID", VENUE)
    email_id = await _seed(factory)

    resp = await _post(client, email_id)

    assert resp.status_code == 502
    assert resp.json()["detail"]["error_type"] == "OpenReviewNoteNotFoundError"
    assert rec.post_calls == []


async def test_permission_error_on_post_surfaces_and_leaves_the_email_unchanged(
    client_and_factory, monkeypatch
):
    client, factory = client_and_factory
    use(monkeypatch, Recorder(post_exc=OpenReviewPermissionError("not a chair")))
    monkeypatch.setattr(settings, "OPENREVIEW_VENUE_ID", VENUE)
    email_id = await _seed(factory)

    resp = await _post(client, email_id)

    assert resp.status_code == 502
    assert resp.json()["detail"]["error_type"] == "OpenReviewPermissionError"

    async with factory() as session:
        email = (
            await session.execute(select(Email).where(Email.id == int(email_id)))
        ).scalars().one()
    assert "openreview_post" not in (email.draft or {})
    assert email.status == "DRAFT_GENERATED"


async def test_thread_mismatch_surfaces_as_its_own_error_type(
    client_and_factory, monkeypatch
):
    client, factory = client_and_factory
    use(monkeypatch, Recorder(post_exc=OpenReviewThreadMismatchError("wrong forum")))
    monkeypatch.setattr(settings, "OPENREVIEW_VENUE_ID", VENUE)
    email_id = await _seed(factory)

    resp = await _post(client, email_id)

    assert resp.status_code == 502
    assert resp.json()["detail"]["error_type"] == "OpenReviewThreadMismatchError"


async def test_a_failure_is_audited_and_never_looks_like_a_success(
    client_and_factory, monkeypatch
):
    client, factory = client_and_factory
    use(monkeypatch, Recorder(post_exc=OpenReviewPermissionError("nope")))
    monkeypatch.setattr(settings, "OPENREVIEW_VENUE_ID", VENUE)
    email_id = await _seed(factory)

    await _post(client, email_id)

    actions = await _audit_actions(factory, email_id)
    assert "openreview_post_failed" in actions
    assert "openreview_comment_posted" not in actions


async def test_a_failed_post_can_be_retried(client_and_factory, monkeypatch):
    """Nothing was recorded, so the idempotency guard must not block a retry."""
    client, factory = client_and_factory
    use(monkeypatch, Recorder(post_exc=OpenReviewPermissionError("transient")))
    monkeypatch.setattr(settings, "OPENREVIEW_VENUE_ID", VENUE)
    email_id = await _seed(factory)
    assert (await _post(client, email_id)).status_code == 502

    rec2 = use(monkeypatch, Recorder())
    resp = await _post(client, email_id)

    assert resp.status_code == 200
    assert len(rec2.post_calls) == 1


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
async def test_unconfigured_venue_id_is_reported_not_guessed(
    client_and_factory, rec, monkeypatch
):
    """The venue group id has no source in the email data — see the config note.
    An unset value must fail loudly rather than post somewhere invented."""
    client, factory = client_and_factory
    monkeypatch.setattr(settings, "OPENREVIEW_VENUE_ID", None)
    email_id = await _seed(factory)

    resp = await _post(client, email_id)

    assert resp.status_code == 501
    assert "OPENREVIEW_VENUE_ID" in resp.json()["detail"]["message"]
    assert rec.post_calls == []
