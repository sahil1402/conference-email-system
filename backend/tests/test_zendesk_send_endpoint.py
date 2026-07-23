"""Tests for the Zendesk-aware POST /emails/{id}/send endpoint (Piece 5).

Hermetic: the transport (`zendesk_sender`) is monkeypatched to a fake, so no
real Zendesk call is made; the DB is in-memory async SQLite. The endpoint
function is called directly (no ASGI wiring needed) with a live session.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.v1 import emails as emails_api
from app.core.config import settings
from app.db.database import Base
from app.integrations.zendesk.sender import SendOutcome, ZendeskSendError
from app.models.enums import EmailSource, EmailStatus


@pytest_asyncio.fixture
async def adb():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def reconcile_calls(monkeypatch):
    """Keep the post-send single-ticket re-sync hermetic: stub it so no real
    Zendesk read happens. Returns the list of ticket ids it was called with, so
    a test can assert the reconcile ran (or, by re-patching, that a failure is
    swallowed)."""
    calls: list[int] = []

    async def _fake_refresh(db, ticket_id, **kwargs):
        calls.append(ticket_id)
        return {"zendesk_status": "solved", "new_messages": 0}

    monkeypatch.setattr(emails_api.zendesk_adapter, "refresh_ticket", _fake_refresh)
    return calls


async def _seed(adb, *, status="approved", zendesk_status="open", ticket_id=123, routing=None):
    payload = {
        "sender": "author@university.edu",
        "subject": "Deadline question",
        "body": "When is the deadline?",
        "status": status,
        "source": EmailSource.ZENDESK.value,
        "zendesk_ticket_id": ticket_id,
        "zendesk_status": zendesk_status,
        "zendesk_updated_at": datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc),
        "draft": {"draft_text": "Dear author, the deadline is in the CFP."},
    }
    # Routing lane matters only for the FAQ-lane auto-send path (send gate).
    if routing is not None:
        payload["routing"] = routing
    return await emails_api.email_repo.create_email(adb, payload)


class FakeSender:
    """Records send_reply kwargs; returns a canned outcome or raises."""

    def __init__(self, *, outcome=None, error=None):
        self._outcome = outcome
        self._error = error
        self.calls = []

    async def send_reply(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._outcome


@pytest.mark.asyncio
async def test_internal_note_send_succeeds(adb, monkeypatch):
    email = await _seed(adb)
    fake = FakeSender(
        outcome=SendOutcome(
            mode="internal_note", public=False, tags_added=["ai_drafted"]
        )
    )
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    result = await emails_api.send_email_reply(
        str(email.id), emails_api.SendRequest(public=False), adb
    )

    assert result["status"] == EmailStatus.SENT.value
    assert result["send"]["mode"] == "internal_note"
    assert result["send"]["state"] == "sent"
    # Correct transport args: internal note, no status change, ai_drafted tag.
    call = fake.calls[0]
    assert call["public"] is False
    assert call["set_status"] is None
    assert call["tags"] == ["ai_drafted"]
    assert call["ticket_id"] == 123
    # DB reflects sent.
    refreshed = await emails_api.email_repo.get_email_by_id(adb, str(email.id))
    assert refreshed.status == EmailStatus.SENT.value


@pytest.mark.asyncio
async def test_public_reply_blocked_unless_allow_auto_send(adb, monkeypatch):
    """An UNREVIEWED (draft_generated) public reply is blocked when the flag is off.

    Truth-table case 4. NOTE the status code is **409 (send gate)**, not 403: the
    send gate refuses a non-approved draft before the public-reply gate is ever
    reached (with ALLOW_AUTO_SEND off it never authorizes draft_generated). The
    security property — no unreviewed email goes public without the flag — holds;
    only the gate that enforces it differs. (Previously this test seeded an
    *approved* email and asserted 403; post-decouple, approved public sends are
    allowed regardless of the flag, so that assertion was inverted — see
    test_approved_public_allowed_without_flag.)
    """
    email = await _seed(adb, status="draft_generated")
    fake = FakeSender(outcome=SendOutcome(mode="public_reply", public=True))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)
    monkeypatch.setattr(settings, "ALLOW_AUTO_SEND", False)

    with pytest.raises(HTTPException) as exc:
        await emails_api.send_email_reply(
            str(email.id), emails_api.SendRequest(public=True), adb
        )
    assert exc.value.status_code == 409  # send gate refuses the unreviewed draft
    assert fake.calls == []  # never reached the transport


@pytest.mark.asyncio
async def test_approved_public_allowed_without_flag(adb, monkeypatch):
    """Truth-table case 1 (NEW behavior): an APPROVED draft may go public even
    with ALLOW_AUTO_SEND=False — the chair's approval IS the authorization."""
    email = await _seed(adb, status="approved")
    fake = FakeSender(
        outcome=SendOutcome(
            mode="public_reply", public=True, status_set="solved",
            tags_added=["ai_auto_replied"],
        )
    )
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)
    monkeypatch.setattr(settings, "ALLOW_AUTO_SEND", False)

    result = await emails_api.send_email_reply(
        str(email.id), emails_api.SendRequest(public=True), adb
    )
    assert result["send"]["mode"] == "public_reply"
    assert result["send"]["state"] == "sent"
    call = fake.calls[0]
    assert call["public"] is True  # actually sent public despite the flag being off


@pytest.mark.asyncio
async def test_approved_internal_note_regardless_of_flag(adb, monkeypatch):
    """Truth-table case 3 (regression guard): an APPROVED internal note (public=
    False) sends fine with ALLOW_AUTO_SEND=False — the decouple didn't disturb it."""
    email = await _seed(adb, status="approved")
    fake = FakeSender(
        outcome=SendOutcome(mode="internal_note", public=False, tags_added=["ai_drafted"])
    )
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)
    monkeypatch.setattr(settings, "ALLOW_AUTO_SEND", False)

    result = await emails_api.send_email_reply(
        str(email.id), emails_api.SendRequest(public=False), adb
    )
    assert result["send"]["mode"] == "internal_note"
    assert result["send"]["state"] == "sent"
    assert fake.calls[0]["public"] is False


@pytest.mark.asyncio
async def test_draft_generated_public_auto_send_with_flag(adb, monkeypatch):
    """Truth-table case 5 (unchanged auto path): a complete FAQ-lane draft_generated
    draft goes public when ALLOW_AUTO_SEND=True — the send gate's auto path."""
    email = await _seed(
        adb, status="draft_generated", routing={"lane": "faq"}
    )
    fake = FakeSender(
        outcome=SendOutcome(
            mode="public_reply", public=True, status_set="solved",
            tags_added=["ai_auto_replied"],
        )
    )
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)
    monkeypatch.setattr(settings, "ALLOW_AUTO_SEND", True)

    result = await emails_api.send_email_reply(
        str(email.id), emails_api.SendRequest(public=True), adb
    )
    assert result["send"]["mode"] == "public_reply"
    assert result["send"]["state"] == "sent"
    assert fake.calls[0]["public"] is True


@pytest.mark.asyncio
async def test_public_reply_allowed_when_flag_on(adb, monkeypatch):
    email = await _seed(adb)
    fake = FakeSender(
        outcome=SendOutcome(
            mode="public_reply", public=True, status_set="solved",
            tags_added=["ai_auto_replied"],
        )
    )
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)
    monkeypatch.setattr(settings, "ALLOW_AUTO_SEND", True)

    result = await emails_api.send_email_reply(
        str(email.id), emails_api.SendRequest(public=True), adb
    )
    call = fake.calls[0]
    assert call["public"] is True
    assert call["set_status"] == "solved"
    assert call["tags"] == ["ai_auto_replied"]
    assert result["send"]["mode"] == "public_reply"


@pytest.mark.asyncio
async def test_closed_ticket_write_rejected(adb, monkeypatch):
    email = await _seed(adb, zendesk_status="closed")
    fake = FakeSender(outcome=SendOutcome(mode="internal_note", public=False))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    with pytest.raises(HTTPException) as exc:
        await emails_api.send_email_reply(
            str(email.id), emails_api.SendRequest(), adb
        )
    assert exc.value.status_code == 409
    assert "closed" in str(exc.value.detail).lower()
    assert fake.calls == []  # never attempted a write
    refreshed = await emails_api.email_repo.get_email_by_id(adb, str(email.id))
    assert refreshed.status == "approved"  # unchanged


@pytest.mark.asyncio
async def test_zendesk_failure_marks_send_failed_not_sent(adb, monkeypatch):
    email = await _seed(adb)
    fake = FakeSender(error=ZendeskSendError("boom", status_code=500))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    with pytest.raises(HTTPException) as exc:
        await emails_api.send_email_reply(
            str(email.id), emails_api.SendRequest(), adb
        )
    assert exc.value.status_code == 502
    refreshed = await emails_api.email_repo.get_email_by_id(adb, str(email.id))
    assert refreshed.status == EmailStatus.SEND_FAILED.value  # NOT "sent"
    assert refreshed.draft["send"]["state"] == "failed"
    assert refreshed.draft["draft_text"]  # draft preserved for retry


@pytest.mark.asyncio
async def test_tag_conflict_surfaces_warning_but_reply_sent(adb, monkeypatch):
    email = await _seed(adb)
    fake = FakeSender(
        outcome=SendOutcome(
            mode="internal_note", public=False, tags_added=[], tag_conflict=True
        )
    )
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    result = await emails_api.send_email_reply(
        str(email.id), emails_api.SendRequest(), adb
    )
    assert result["status"] == EmailStatus.SENT.value
    assert "warning" in result
    assert result["send"]["tag_conflict"] is True


# --- target_status routing into set_status (Piece B-impl-1b) ---------------


@pytest.mark.asyncio
async def test_target_status_overrides_internal_note_default(adb, monkeypatch):
    """target_status="pending" on an internal note → set_status "pending", not None.

    Proves an explicit target_status wins over the internal-note default (which,
    with no target_status, would leave status unchanged → set_status=None).
    """
    email = await _seed(adb)
    fake = FakeSender(
        outcome=SendOutcome(mode="internal_note", public=False, status_set="pending")
    )
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    await emails_api.send_email_reply(
        str(email.id),
        emails_api.SendRequest(public=False, target_status="pending"),
        adb,
    )
    call = fake.calls[0]
    assert call["public"] is False
    assert call["set_status"] == "pending"  # NOT None
    assert call["tags"] == ["ai_drafted"]  # tags unaffected by target_status


@pytest.mark.asyncio
async def test_target_status_overrides_public_reply_default(adb, monkeypatch):
    """target_status="open" on a public reply → set_status "open", not "solved".

    Proves an explicit target_status wins over the public-reply default of
    "solved". Requires ALLOW_AUTO_SEND for the public path.
    """
    email = await _seed(adb)
    fake = FakeSender(
        outcome=SendOutcome(
            mode="public_reply", public=True, status_set="open",
            tags_added=["ai_auto_replied"],
        )
    )
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)
    monkeypatch.setattr(settings, "ALLOW_AUTO_SEND", True)

    await emails_api.send_email_reply(
        str(email.id),
        emails_api.SendRequest(public=True, target_status="open"),
        adb,
    )
    call = fake.calls[0]
    assert call["public"] is True
    assert call["set_status"] == "open"  # NOT "solved"


@pytest.mark.asyncio
async def test_no_target_status_public_defaults_to_solved(adb, monkeypatch):
    """Regression: target_status=None + public=True → set_status "solved" (old default)."""
    email = await _seed(adb)
    fake = FakeSender(
        outcome=SendOutcome(
            mode="public_reply", public=True, status_set="solved",
            tags_added=["ai_auto_replied"],
        )
    )
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)
    monkeypatch.setattr(settings, "ALLOW_AUTO_SEND", True)

    await emails_api.send_email_reply(
        str(email.id), emails_api.SendRequest(public=True), adb
    )
    assert fake.calls[0]["set_status"] == "solved"


@pytest.mark.asyncio
async def test_no_target_status_internal_defaults_to_none(adb, monkeypatch):
    """Regression: target_status=None + public=False → set_status None (old default)."""
    email = await _seed(adb)
    fake = FakeSender(
        outcome=SendOutcome(mode="internal_note", public=False, tags_added=["ai_drafted"])
    )
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    await emails_api.send_email_reply(
        str(email.id), emails_api.SendRequest(public=False), adb
    )
    assert fake.calls[0]["set_status"] is None


def test_invalid_target_status_rejected_by_validation():
    """An out-of-Literal target_status is rejected by Pydantic before the handler.

    Direct handler calls bypass ASGI, so the FastAPI 422 wrapper never runs; the
    faithful equivalent is that constructing the request model raises
    ValidationError — the invalid value can never reach send_email_reply.
    """
    with pytest.raises(ValidationError):
        emails_api.SendRequest(target_status="closed")


@pytest.mark.asyncio
async def test_target_status_does_not_change_tags(adb, monkeypatch):
    """Tags key off want_public only: target_status="pending" + public=True still
    tags ["ai_auto_replied"] (target_status affects status, never tags)."""
    email = await _seed(adb)
    fake = FakeSender(
        outcome=SendOutcome(
            mode="public_reply", public=True, status_set="pending",
            tags_added=["ai_auto_replied"],
        )
    )
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)
    monkeypatch.setattr(settings, "ALLOW_AUTO_SEND", True)

    await emails_api.send_email_reply(
        str(email.id),
        emails_api.SendRequest(public=True, target_status="pending"),
        adb,
    )
    call = fake.calls[0]
    assert call["tags"] == ["ai_auto_replied"]  # unchanged by target_status
    assert call["set_status"] == "pending"


# --- post-send bucket move + best-effort reconcile -------------------------


@pytest.mark.asyncio
async def test_send_moves_ticket_to_chosen_bucket(adb, monkeypatch, reconcile_calls):
    """Submit-as-Solved (internal note + target_status="solved"): the local
    zendesk_status becomes "solved" so the ticket moves to the solved bucket, the
    response reflects it, and the post-send reconcile runs for that ticket id."""
    email = await _seed(adb, zendesk_status="open")
    fake = FakeSender(
        outcome=SendOutcome(mode="internal_note", public=False, status_set="solved")
    )
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    result = await emails_api.send_email_reply(
        str(email.id),
        emails_api.SendRequest(public=False, target_status="solved"),
        adb,
    )
    assert result["status"] == EmailStatus.SENT.value
    assert result["zendesk_status"] == "solved"       # response shows the bucket move
    assert reconcile_calls == [123]                    # reconcile invoked for the ticket
    refreshed = await emails_api.email_repo.get_email_by_id(adb, str(email.id))
    assert refreshed.zendesk_status == "solved"


@pytest.mark.asyncio
async def test_send_follows_pending_choice(adb, monkeypatch):
    """Submit-as-Pending moves the ticket to the pending bucket (not solved)."""
    email = await _seed(adb, zendesk_status="open")
    fake = FakeSender(
        outcome=SendOutcome(mode="internal_note", public=False, status_set="pending")
    )
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    result = await emails_api.send_email_reply(
        str(email.id),
        emails_api.SendRequest(public=False, target_status="pending"),
        adb,
    )
    assert result["zendesk_status"] == "pending"
    refreshed = await emails_api.email_repo.get_email_by_id(adb, str(email.id))
    assert refreshed.zendesk_status == "pending"


@pytest.mark.asyncio
async def test_send_no_status_change_keeps_bucket(adb, monkeypatch):
    """Internal note with no target_status → set_status None → bucket unchanged
    (no optimistic write); the reply still posts."""
    email = await _seed(adb, zendesk_status="open")
    fake = FakeSender(outcome=SendOutcome(mode="internal_note", public=False))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    result = await emails_api.send_email_reply(
        str(email.id), emails_api.SendRequest(public=False), adb
    )
    assert result["zendesk_status"] == "open"          # unchanged
    refreshed = await emails_api.email_repo.get_email_by_id(adb, str(email.id))
    assert refreshed.zendesk_status == "open"


@pytest.mark.asyncio
async def test_reconcile_failure_does_not_fail_send(adb, monkeypatch):
    """If the post-send re-sync raises, the send still succeeds (the reply is
    already posted) and the optimistic bucket move stands."""
    email = await _seed(adb, zendesk_status="open")
    fake = FakeSender(
        outcome=SendOutcome(mode="internal_note", public=False, status_set="solved")
    )
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    async def _boom(db, ticket_id, **kwargs):
        raise RuntimeError("zendesk unreachable")

    monkeypatch.setattr(emails_api.zendesk_adapter, "refresh_ticket", _boom)

    result = await emails_api.send_email_reply(
        str(email.id),
        emails_api.SendRequest(public=False, target_status="solved"),
        adb,
    )
    assert result["status"] == EmailStatus.SENT.value  # send NOT marked failed
    assert result["zendesk_status"] == "solved"         # optimistic move stands


@pytest.mark.asyncio
async def test_reconcile_db_failure_recovers_and_still_sends(adb, monkeypatch):
    """If the reconcile poisons the session (a failed DB statement) and then
    raises, the endpoint rolls back and STILL returns SENT — the reply is already
    posted to Zendesk — and the optimistic bucket move stands."""
    from sqlalchemy import text

    email = await _seed(adb, zendesk_status="open")
    fake = FakeSender(
        outcome=SendOutcome(mode="internal_note", public=False, status_set="solved")
    )
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    async def _poison(db, ticket_id, **kwargs):
        # Leave the AsyncSession in a pending-rollback state, then raise (as a
        # real refresh_ticket DB failure would).
        try:
            await db.execute(text("SELECT * FROM does_not_exist"))
        except Exception:
            pass
        raise RuntimeError("boom after poisoning the session")

    monkeypatch.setattr(emails_api.zendesk_adapter, "refresh_ticket", _poison)

    result = await emails_api.send_email_reply(
        str(email.id),
        emails_api.SendRequest(public=False, target_status="solved"),
        adb,
    )
    assert result["status"] == EmailStatus.SENT.value   # recovered, not a 500
    assert result["zendesk_status"] == "solved"          # optimistic move survived


@pytest.mark.asyncio
async def test_non_zendesk_email_still_501(adb, monkeypatch):
    email = await emails_api.email_repo.create_email(
        adb,
        {
            "sender": "a@b.org",
            "subject": "s",
            "body": "b",
            "status": "approved",
            "source": EmailSource.TOY_DATASET.value,
            "draft": {"draft_text": "some reply"},
        },
    )
    fake = FakeSender(outcome=SendOutcome(mode="internal_note", public=False))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    with pytest.raises(HTTPException) as exc:
        await emails_api.send_email_reply(str(email.id), emails_api.SendRequest(), adb)
    assert exc.value.status_code == 501
    assert fake.calls == []
