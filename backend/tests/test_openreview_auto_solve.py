"""Auto-solving the Zendesk ticket after a successful OpenReview post.

THE PROPERTY UNDER TEST is that three outcomes stay distinguishable. Once the
OpenReview comment is posted it is public and cannot be withdrawn from here, so:

* a Zendesk failure afterwards must NOT read as "the action failed" — a chair
  who believed that would retry, and either be blocked by the idempotency gate
  or post a duplicate; and
* it must NOT read as plain success either — the ticket really is still open and
  somebody has to close it.

Hermetic: the OpenReview seams and ``zendesk_sender`` are monkeypatched on the
endpoint module, so neither service is contacted.
"""

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
from app.integrations.openreview import OpenReviewNote, OpenReviewPermissionError, PostedComment
from app.integrations.zendesk.sender import SendOutcome, ZendeskSendError

NOTE_ID = "jnHgRMHgrm"
FORUM_ID = "ll0avn6ylq"
VENUE = "AAAI.org/2027/Conference"
TICKET = 21567
URL = "/api/v1/emails/{id}/post-openreview-reply"


def _extraction(**overrides) -> dict:
    base = {
        "submission_numbers": ["1030"],
        "openreview_forum_ids": [FORUM_ID],
        "openreview_note_id": NOTE_ID,
        "openreview_notification_sender": "aaai2027-notifications@openreview.net",
        "extracted_reply_text": "I will review before the deadline.",
        "authors": [],
        "method": "llm_distiller",
        "openreview_reply_candidate": True,
    }
    base.update(overrides)
    return base


# --- doubles ----------------------------------------------------------------


class FakeSender:
    """Stands in for ZendeskSender, recording set_status_only calls."""

    def __init__(self, *, exc=None):
        self._exc = exc
        self.calls = []

    async def set_status_only(self, *, ticket_id, status, tags, updated_stamp):
        self.calls.append(
            {"ticket_id": ticket_id, "status": status, "tags": tags,
             "updated_stamp": updated_stamp}
        )
        if self._exc is not None:
            raise self._exc
        return SendOutcome(
            mode="status_only", public=False, status_set=status, tags_added=list(tags)
        )


class FakeAdapter:
    async def refresh_ticket(self, db, ticket_id):
        return None


@pytest.fixture
def seams(monkeypatch):
    """OpenReview always succeeds here; the Zendesk half is what varies."""
    posted = PostedComment(edit_id="edit-1", note_id="new-note-1")
    monkeypatch.setattr(emails_module, "get_openreview_client", lambda s: object())
    monkeypatch.setattr(
        emails_module,
        "openreview_get_note",
        lambda c, nid: OpenReviewNote(id=NOTE_ID, forum=FORUM_ID, readers=["r/1"], content={}),
    )
    monkeypatch.setattr(
        emails_module, "openreview_post_comment_reply", lambda c, **kw: posted
    )
    monkeypatch.setattr(emails_module, "zendesk_adapter", FakeAdapter())
    monkeypatch.setattr(settings, "OPENREVIEW_VENUE_ID", VENUE)

    def install(sender):
        monkeypatch.setattr(emails_module, "zendesk_sender", sender)
        return sender

    return install


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
        "subject": "Re: [AAAI 2027] SPC commented",
        "body": "text",
        "status": "DRAFT_GENERATED",
        "routing": {"lane": "human_review"},
        "draft": {"draft_text": "an AI draft"},
        "extraction": _extraction(),
        "source": "zendesk",
        "zendesk_ticket_id": TICKET,
        "zendesk_status": "open",
    }
    fields.update(overrides)
    async with factory() as session:
        email = Email(**fields)
        session.add(email)
        await session.commit()
        await session.refresh(email)
        return str(email.id)


async def _audit(factory, email_id):
    async with factory() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.email_id == int(email_id))
            )
        ).scalars().all()
        return {r.action: r.extra_metadata for r in rows}


async def _email(factory, email_id) -> Email:
    async with factory() as session:
        return (
            await session.execute(select(Email).where(Email.id == int(email_id)))
        ).scalars().one()


async def _post(client, email_id):
    return await client.post(
        URL.format(id=email_id),
        json={"reply_text": "Thanks, will do.", "submission_number": 1030,
              "posted_by": "chair1"},
    )


# ---------------------------------------------------------------------------
# (b) full success
# ---------------------------------------------------------------------------
async def test_full_success_posts_and_solves(client_and_factory, seams):
    client, factory = client_and_factory
    sender = seams(FakeSender())
    email_id = await _seed(factory)

    resp = await _post(client, email_id)

    assert resp.status_code == 200
    body = resp.json()
    assert body["openreview_post"]["state"] == "posted"
    assert body["ticket_resolution"]["outcome"] == "solved"
    assert body["ticket_resolution"]["zendesk_status"] == "solved"
    assert body["ticket_resolution"]["error"] is None
    assert body["warning"] is None


async def test_full_success_calls_set_status_only_with_solved(client_and_factory, seams):
    """Reuses the same transport /set-status drives — no HTTP hop."""
    client, factory = client_and_factory
    sender = seams(FakeSender())
    email_id = await _seed(factory)

    await _post(client, email_id)

    assert sender.calls == [
        {"ticket_id": TICKET, "status": "solved",
         "tags": ["ai_status_solved"], "updated_stamp": None}
    ]


async def test_full_success_records_both_audit_entries(client_and_factory, seams):
    client, factory = client_and_factory
    seams(FakeSender())
    email_id = await _seed(factory)

    await _post(client, email_id)

    actions = await _audit(factory, email_id)
    assert "openreview_comment_posted" in actions
    assert "zendesk_auto_solved" in actions
    assert actions["zendesk_auto_solved"]["status_set"] == "solved"
    assert actions["zendesk_auto_solved"]["zendesk_ticket_id"] == TICKET


async def test_full_success_marks_the_email_solved(client_and_factory, seams):
    client, factory = client_and_factory
    seams(FakeSender())
    email_id = await _seed(factory)

    await _post(client, email_id)

    email = await _email(factory, email_id)
    assert email.status == "SOLVED"
    assert email.zendesk_status == "solved"
    assert email.draft["openreview_post"]["state"] == "posted"


# ---------------------------------------------------------------------------
# (a) OpenReview post fails — commit 11 behaviour, Zendesk never touched
# ---------------------------------------------------------------------------
async def test_openreview_failure_never_touches_zendesk(
    client_and_factory, seams, monkeypatch
):
    client, factory = client_and_factory
    sender = seams(FakeSender())

    def boom(c, **kw):
        raise OpenReviewPermissionError("not a chair")

    monkeypatch.setattr(emails_module, "openreview_post_comment_reply", boom)
    email_id = await _seed(factory)

    resp = await _post(client, email_id)

    assert resp.status_code == 502
    assert sender.calls == [], "no solve may be attempted when nothing was posted"

    email = await _email(factory, email_id)
    assert email.status == "DRAFT_GENERATED"
    assert email.zendesk_status == "open"
    assert "openreview_post" not in (email.draft or {})

    actions = await _audit(factory, email_id)
    assert "openreview_post_failed" in actions
    assert "zendesk_auto_solved" not in actions
    assert "zendesk_auto_solve_failed" not in actions


# ---------------------------------------------------------------------------
# (c) posted, but NOT solved — the partial state
# ---------------------------------------------------------------------------
async def test_partial_success_is_reported_as_posted_but_not_solved(
    client_and_factory, seams
):
    """THE central assertion: a 200 that says, unambiguously, 'posted, not
    solved' — neither a generic success nor an error."""
    client, factory = client_and_factory
    seams(FakeSender(exc=ZendeskSendError("Zendesk 503", status_code=503)))
    email_id = await _seed(factory)

    resp = await _post(client, email_id)

    assert resp.status_code == 200, "the comment IS posted; this is not a failure"
    body = resp.json()
    assert body["openreview_post"]["state"] == "posted"
    res = body["ticket_resolution"]
    assert res["outcome"] == "solve_failed"
    assert res["attempted"] is True
    assert res["zendesk_status"] is None, "the ticket must not be reported solved"
    assert res["error_type"] == "ZendeskSendError"
    assert "503" in res["error"]


async def test_partial_success_carries_an_actionable_recovery_message(
    client_and_factory, seams
):
    client, factory = client_and_factory
    seams(FakeSender(exc=ZendeskSendError("down", status_code=503)))
    email_id = await _seed(factory)

    body = (await _post(client, email_id)).json()

    recovery = body["ticket_resolution"]["recovery"]
    assert "WAS posted" in recovery
    assert "still open" in recovery
    assert "do not re-post" in recovery.lower()
    # The same text surfaces as the top-level warning, matching /send's shape.
    assert body["warning"] == recovery


async def test_partial_success_records_both_audit_entries(client_and_factory, seams):
    """One success and one failure, not a single ambiguous entry."""
    client, factory = client_and_factory
    seams(FakeSender(exc=ZendeskSendError("boom", status_code=500)))
    email_id = await _seed(factory)

    await _post(client, email_id)

    actions = await _audit(factory, email_id)
    assert "openreview_comment_posted" in actions
    assert "zendesk_auto_solve_failed" in actions
    assert "zendesk_auto_solved" not in actions


async def test_the_audit_trail_alone_explains_the_partial_state(
    client_and_factory, seams
):
    """Someone reading only the audit trail must be able to say what state this
    is in — which comment is live, which ticket is open, and why — without
    cross-referencing the response they never saw."""
    client, factory = client_and_factory
    seams(FakeSender(exc=ZendeskSendError("Service Unavailable", status_code=503)))
    email_id = await _seed(factory)

    await _post(client, email_id)
    actions = await _audit(factory, email_id)

    posted = actions["openreview_comment_posted"]
    assert posted["note_id"] == "new-note-1"
    assert posted["forum_id"] == FORUM_ID

    failed = actions["zendesk_auto_solve_failed"]
    assert failed["note_id"] == "new-note-1"           # which comment is live
    assert failed["zendesk_ticket_id"] == TICKET       # which ticket is open
    assert failed["requested_status"] == "solved"
    assert failed["status_code"] == 503                # why it failed
    assert failed["openreview_post_state"] == "posted"
    assert failed["ticket_state"] == "not solved"


async def test_partial_success_leaves_the_ticket_state_untouched(
    client_and_factory, seams
):
    """Nothing may claim the ticket moved when it did not."""
    client, factory = client_and_factory
    seams(FakeSender(exc=ZendeskSendError("nope", status_code=500)))
    email_id = await _seed(factory, zendesk_status="open")

    await _post(client, email_id)

    email = await _email(factory, email_id)
    assert email.zendesk_status == "open"
    assert email.status != "SOLVED"
    assert email.draft["ticket_resolution"]["state"] == "solve_failed"


async def test_partial_success_does_not_mark_the_email_send_failed(
    client_and_factory, seams
):
    """Deliberate divergence from /set-status: SEND_FAILED would drop an email
    whose reply is already live into the failed-send bucket and invite a retry
    of the wrong thing."""
    client, factory = client_and_factory
    seams(FakeSender(exc=ZendeskSendError("nope", status_code=500)))
    email_id = await _seed(factory, status="DRAFT_GENERATED")

    await _post(client, email_id)

    assert (await _email(factory, email_id)).status == "DRAFT_GENERATED"


# ---------------------------------------------------------------------------
# Skips — no ticket, closed ticket
# ---------------------------------------------------------------------------
async def test_non_zendesk_email_skips_the_solve_without_failing(
    client_and_factory, seams
):
    client, factory = client_and_factory
    sender = seams(FakeSender())
    email_id = await _seed(
        factory, source="toy_dataset", zendesk_ticket_id=None, zendesk_status=None
    )

    resp = await _post(client, email_id)

    assert resp.status_code == 200
    assert resp.json()["ticket_resolution"]["outcome"] == "skipped_no_ticket"
    assert sender.calls == []
    assert "zendesk_auto_solve_skipped" in await _audit(factory, email_id)


async def test_closed_ticket_skips_the_solve_without_failing(client_and_factory, seams):
    """Closed tickets are immutable — but the post still happened, so this is
    not an error."""
    client, factory = client_and_factory
    sender = seams(FakeSender())
    email_id = await _seed(factory, zendesk_status="closed")

    resp = await _post(client, email_id)

    assert resp.status_code == 200
    assert resp.json()["ticket_resolution"]["outcome"] == "skipped_closed"
    assert sender.calls == []


# ---------------------------------------------------------------------------
# Point 7: what a chair can do after outcome (c)
# ---------------------------------------------------------------------------
async def test_reposting_after_a_partial_success_is_still_blocked(
    client_and_factory, seams
):
    """Commit 11's idempotency gate is UNCHANGED and still refuses — correctly.
    The comment is live; re-posting would duplicate it."""
    client, factory = client_and_factory
    seams(FakeSender(exc=ZendeskSendError("down", status_code=503)))
    email_id = await _seed(factory)

    first = await _post(client, email_id)
    second = await _post(client, email_id)

    assert first.status_code == 200
    assert first.json()["ticket_resolution"]["outcome"] == "solve_failed"
    assert second.status_code == 409
    assert "already been posted" in second.json()["detail"]["reason"]


async def test_the_solve_can_still_be_retried_via_set_status(
    client_and_factory, seams, monkeypatch
):
    """The recovery path, PROVEN rather than asserted in a comment.

    The idempotency gate blocks re-posting, which also blocks retrying the solve
    through THIS endpoint — acceptable only because /set-status already exists
    and does exactly 'solve the ticket, no reply'. This drives that path to
    confirm it works from the partial state.
    """
    client, factory = client_and_factory
    seams(FakeSender(exc=ZendeskSendError("down", status_code=503)))
    email_id = await _seed(factory)
    await _post(client, email_id)
    assert (await _email(factory, email_id)).zendesk_status == "open"

    # Zendesk recovers; the chair marks it solved with the existing endpoint.
    working = FakeSender()
    monkeypatch.setattr(emails_module, "zendesk_sender", working)
    resp = await client.post(
        f"/api/v1/emails/{email_id}/set-status",
        json={"status": "solved", "set_by": "chair1"},
    )

    assert resp.status_code == 200
    assert working.calls[0]["status"] == "solved"
    email = await _email(factory, email_id)
    assert email.status == "SOLVED"
    assert email.zendesk_status == "solved"
