"""Tests for POST /emails/{id}/set-status — set a ticket's Zendesk status
(new / open / pending / solved) with NO reply.

Hermetic, mirroring test_zendesk_send_endpoint.py: the transport
(`zendesk_sender`) is monkeypatched to a fake so no real Zendesk call happens;
the DB is in-memory async SQLite; the endpoint function is called directly.
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


@pytest.fixture(autouse=True)
def reconcile_calls(monkeypatch):
    """Keep the post-set single-ticket re-sync hermetic: stub it so no real
    Zendesk read happens. Returns the ticket ids it was called with."""
    calls: list[int] = []

    async def _fake_refresh(db, ticket_id, **kwargs):
        calls.append(ticket_id)
        return {"zendesk_status": "solved", "new_messages": 0}

    monkeypatch.setattr(emails_api.zendesk_adapter, "refresh_ticket", _fake_refresh)
    return calls


async def _seed(adb, *, status="draft_generated", zendesk_status="open",
                ticket_id=123, source=None, draft=None):
    payload = {
        "sender": "author@university.edu",
        "subject": "Spam / no-response-needed",
        "body": "unsubscribe",
        "status": status,
        "source": EmailSource.ZENDESK.value if source is None else source,
        "zendesk_ticket_id": ticket_id if source in (None, EmailSource.ZENDESK.value) else None,
        "zendesk_status": zendesk_status,
        "zendesk_updated_at": datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc),
        "draft": {"draft_text": "Dear author, ..."} if draft is None else draft,
    }
    return await emails_api.email_repo.create_email(adb, payload)


class FakeSolveSender:
    """Records set_status_only kwargs; returns a canned outcome or raises."""

    def __init__(self, *, outcome=None, error=None):
        self._outcome = outcome
        self._error = error
        self.calls = []

    async def set_status_only(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._outcome


def _outcome(status="solved", *, tags_added=None, tag_conflict=False):
    return SendOutcome(
        mode="status_only",
        public=False,
        status_set=status,
        tags_added=tags_added if tags_added is not None else [f"ai_status_{status}"],
        tag_conflict=tag_conflict,
    )


@pytest.mark.asyncio
async def test_solved_no_reply_succeeds(adb, monkeypatch, reconcile_calls):
    email = await _seed(adb, zendesk_status="open")
    fake = FakeSolveSender(outcome=_outcome("solved"))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    result = await emails_api.set_ticket_status_no_reply(
        str(email.id), emails_api.SetStatusRequest(status="solved"), adb
    )

    assert result["status"] == EmailStatus.SOLVED.value  # solved is terminal
    assert result["send"]["state"] == "status_set_no_reply"
    assert result["send"]["mode"] == "status_only"
    assert result["send"]["public"] is False
    # Correct transport args: solved status + the per-status no-reply tag.
    call = fake.calls[0]
    assert call["ticket_id"] == 123
    assert call["status"] == "solved"
    assert call["tags"] == ["ai_status_solved"]
    refreshed = await emails_api.email_repo.get_email_by_id(adb, str(email.id))
    assert refreshed.status == EmailStatus.SOLVED.value


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["new", "open", "pending"])
async def test_new_open_move_bucket_but_keep_workflow_status(
    adb, monkeypatch, reconcile_calls, target
):
    """new / open / pending aren't resolutions: the ticket bucket moves
    (zendesk_status), but the email's workflow status is left unchanged (not
    SOLVED). Seeded as 'hold' so no target equals the starting bucket — an
    equal one would make the "bucket moved" assertion vacuously true."""
    email = await _seed(adb, status="draft_generated", zendesk_status="hold")
    fake = FakeSolveSender(outcome=_outcome(target))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    result = await emails_api.set_ticket_status_no_reply(
        str(email.id), emails_api.SetStatusRequest(status=target), adb
    )
    assert result["status"] == "draft_generated"     # workflow status unchanged
    assert result["zendesk_status"] == target          # bucket moved
    assert fake.calls[0]["tags"] == [f"ai_status_{target}"]
    refreshed = await emails_api.email_repo.get_email_by_id(adb, str(email.id))
    assert refreshed.status == "draft_generated"
    assert refreshed.zendesk_status == target


@pytest.mark.asyncio
async def test_set_status_moves_ticket_bucket_and_reconciles(adb, monkeypatch, reconcile_calls):
    email = await _seed(adb, zendesk_status="open")
    fake = FakeSolveSender(outcome=_outcome("solved"))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    result = await emails_api.set_ticket_status_no_reply(
        str(email.id), emails_api.SetStatusRequest(status="solved"), adb
    )
    assert result["zendesk_status"] == "solved"   # response shows the bucket move
    assert reconcile_calls == [123]                # reconcile invoked for the ticket
    refreshed = await emails_api.email_repo.get_email_by_id(adb, str(email.id))
    assert refreshed.zendesk_status == "solved"


@pytest.mark.asyncio
async def test_set_status_works_regardless_of_draft_or_placeholders(adb, monkeypatch):
    """Setting status skips the reply, so an unresolved [CHAIR: …] placeholder
    (or a bare draft) never blocks it — unlike approve/send."""
    email = await _seed(
        adb,
        status="draft_generated",
        draft={"draft_text": "Dear author, [CHAIR: decide the deadline]"},
    )
    fake = FakeSolveSender(outcome=_outcome("solved"))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    result = await emails_api.set_ticket_status_no_reply(
        str(email.id), emails_api.SetStatusRequest(status="solved"), adb
    )
    assert result["status"] == EmailStatus.SOLVED.value
    assert len(fake.calls) == 1  # reached the transport despite the placeholder


@pytest.mark.asyncio
async def test_set_status_closed_ticket_rejected(adb, monkeypatch):
    email = await _seed(adb, zendesk_status="closed")
    fake = FakeSolveSender(outcome=_outcome("solved"))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    with pytest.raises(HTTPException) as exc:
        await emails_api.set_ticket_status_no_reply(
            str(email.id), emails_api.SetStatusRequest(status="solved"), adb
        )
    assert exc.value.status_code == 409
    assert "closed" in str(exc.value.detail).lower()
    assert fake.calls == []  # never attempted a write
    refreshed = await emails_api.email_repo.get_email_by_id(adb, str(email.id))
    assert refreshed.status == "draft_generated"  # unchanged


@pytest.mark.asyncio
async def test_set_status_non_zendesk_501(adb, monkeypatch):
    email = await emails_api.email_repo.create_email(
        adb,
        {
            "sender": "a@b.org",
            "subject": "s",
            "body": "b",
            "status": "draft_generated",
            "source": EmailSource.TOY_DATASET.value,
            "draft": {"draft_text": "x"},
        },
    )
    fake = FakeSolveSender(outcome=_outcome("solved"))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    with pytest.raises(HTTPException) as exc:
        await emails_api.set_ticket_status_no_reply(
            str(email.id), emails_api.SetStatusRequest(status="solved"), adb
        )
    assert exc.value.status_code == 501
    assert fake.calls == []


@pytest.mark.asyncio
async def test_set_status_missing_email_404(adb, monkeypatch):
    fake = FakeSolveSender(outcome=_outcome("solved"))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)
    with pytest.raises(HTTPException) as exc:
        await emails_api.set_ticket_status_no_reply(
            "999999", emails_api.SetStatusRequest(status="solved"), adb
        )
    assert exc.value.status_code == 404


def test_invalid_status_rejected_by_validation():
    """An out-of-Literal status is rejected by Pydantic before the handler."""
    with pytest.raises(ValidationError):
        emails_api.SetStatusRequest(status="closed")


@pytest.mark.asyncio
async def test_set_status_zendesk_failure_marks_send_failed(adb, monkeypatch):
    email = await _seed(adb, zendesk_status="open")
    fake = FakeSolveSender(error=ZendeskSendError("boom", status_code=500))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    with pytest.raises(HTTPException) as exc:
        await emails_api.set_ticket_status_no_reply(
            str(email.id), emails_api.SetStatusRequest(status="solved"), adb
        )
    assert exc.value.status_code == 502
    refreshed = await emails_api.email_repo.get_email_by_id(adb, str(email.id))
    assert refreshed.status == EmailStatus.SEND_FAILED.value  # NOT solved
    assert refreshed.draft["send"]["state"] == "failed"


@pytest.mark.asyncio
async def test_set_status_reconcile_failure_does_not_fail(adb, monkeypatch):
    """If the post-set re-sync raises, the status change still succeeds (already
    set in Zendesk) and the optimistic bucket move stands."""
    email = await _seed(adb, zendesk_status="open")
    fake = FakeSolveSender(outcome=_outcome("solved"))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    async def _boom(db, ticket_id, **kwargs):
        raise RuntimeError("zendesk unreachable")

    monkeypatch.setattr(emails_api.zendesk_adapter, "refresh_ticket", _boom)

    result = await emails_api.set_ticket_status_no_reply(
        str(email.id), emails_api.SetStatusRequest(status="solved"), adb
    )
    assert result["status"] == EmailStatus.SOLVED.value  # NOT marked failed
    assert result["zendesk_status"] == "solved"           # optimistic move stands


@pytest.mark.asyncio
async def test_set_status_tag_conflict_surfaces_warning(adb, monkeypatch):
    email = await _seed(adb, zendesk_status="open")
    fake = FakeSolveSender(outcome=_outcome("solved", tags_added=[], tag_conflict=True))
    monkeypatch.setattr(emails_api, "zendesk_sender", fake)

    result = await emails_api.set_ticket_status_no_reply(
        str(email.id), emails_api.SetStatusRequest(status="solved"), adb
    )
    assert result["status"] == EmailStatus.SOLVED.value
    assert "warning" in result
    assert result["send"]["tag_conflict"] is True
