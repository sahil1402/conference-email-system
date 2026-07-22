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


async def _seed(adb, *, status="approved", zendesk_status="open", ticket_id=123):
    return await emails_api.email_repo.create_email(
        adb,
        {
            "sender": "author@university.edu",
            "subject": "Deadline question",
            "body": "When is the deadline?",
            "status": status,
            "source": EmailSource.ZENDESK.value,
            "zendesk_ticket_id": ticket_id,
            "zendesk_status": zendesk_status,
            "zendesk_updated_at": datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc),
            "draft": {"draft_text": "Dear author, the deadline is in the CFP."},
        },
    )


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
    email = await _seed(adb)
    fake = FakeSender(outcome=SendOutcome(mode="public_reply", public=True))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)
    monkeypatch.setattr(emails_api.settings, "ALLOW_AUTO_SEND", False)

    with pytest.raises(HTTPException) as exc:
        await emails_api.send_email_reply(
            str(email.id), emails_api.SendRequest(public=True), adb
        )
    assert exc.value.status_code == 403
    assert fake.calls == []  # never reached the transport


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
    monkeypatch.setattr(emails_api.settings, "ALLOW_AUTO_SEND", True)

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
    monkeypatch.setattr(emails_api.settings, "ALLOW_AUTO_SEND", True)

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
    monkeypatch.setattr(emails_api.settings, "ALLOW_AUTO_SEND", True)

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
    monkeypatch.setattr(emails_api.settings, "ALLOW_AUTO_SEND", True)

    await emails_api.send_email_reply(
        str(email.id),
        emails_api.SendRequest(public=True, target_status="pending"),
        adb,
    )
    call = fake.calls[0]
    assert call["tags"] == ["ai_auto_replied"]  # unchanged by target_status
    assert call["set_status"] == "pending"


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
