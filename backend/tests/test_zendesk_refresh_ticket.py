"""Tests for ZendeskIngestAdapter.refresh_ticket — the targeted single-ticket
reconcile used after a chair send. Fully hermetic: no real Zendesk, in-memory
SQLite. It must (1) update the local zendesk_status to the authoritative value,
(2) append only genuinely-new comments (dedup by comment id) — e.g. the reply
the chair just sent — and (3) never touch the pipeline / reprocess.
"""

from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.models import Email, EmailThreadMessage
from app.integrations.zendesk.adapter import ZendeskIngestAdapter
from app.models.enums import EmailSource, EmailStatus
from app.repositories.email_repository import EmailRepository


async def _nosleep(*_args, **_kwargs):
    return None


class FakeProvider:
    base_url = "https://aaai.zendesk.com/api/v2"

    def get_auth_header(self):
        return {"Authorization": "Bearer test-token"}


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "https://aaai.zendesk.com/x")
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=req,
                response=httpx.Response(self.status_code, request=req),
            )

    def json(self):
        return self._payload


class RefreshFakeClient:
    """Serves GET /tickets/{id}.json and GET /tickets/{id}/comments.json."""

    def __init__(self, ticket, comments_payload, *, ticket_code=200, comments_code=200):
        self.ticket = ticket
        self.comments_payload = comments_payload
        self.ticket_code = ticket_code
        self.comments_code = comments_code
        self.get_calls: list[str] = []

    async def get(self, url, params=None, headers=None):
        self.get_calls.append(url)
        if "/comments" in url:
            return FakeResponse(self.comments_code, self.comments_payload)
        if "/tickets/" in url and url.endswith(".json"):
            return FakeResponse(self.ticket_code, {"ticket": self.ticket})
        return FakeResponse(404, {})

    async def aclose(self):
        return None


def _user(uid, role):
    return {"id": uid, "role": role, "name": f"user{uid}", "email": f"u{uid}@ex.org"}


def _comment(cid, author_id, *, public=True, body="text", created="2026-07-15T09:00:00Z"):
    return {
        "id": cid, "public": public, "author_id": author_id,
        "plain_body": body, "html_body": f"<p>{body}</p>",
        "created_at": created, "via": {"channel": "email"},
    }


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


async def _seed_ticket_email(db, *, ticket_id=777, requester_id=500, status="open"):
    """An Email mapped to a Zendesk ticket, with the requester's opening comment
    already stored as a thread message (comment id 1)."""
    repo = EmailRepository()
    email = await repo.create_email(
        db,
        {
            "sender": "author@uni.edu",
            "subject": "Question about the deadline",
            "body": "original question",
            "status": EmailStatus.DRAFT_GENERATED.value,
            "source": EmailSource.ZENDESK.value,
            "zendesk_ticket_id": ticket_id,
            "zendesk_requester_id": requester_id,
            "zendesk_status": status,
            "last_processed_comment_id": 1,
        },
    )
    await repo.add_thread_messages(
        db, str(email.id),
        [{
            "zendesk_comment_id": 1, "public": True, "author_id": requester_id,
            "author_role": "end-user", "plain_body": "original question",
            "html_body": "<p>original question</p>",
            "created_at": datetime(2026, 7, 15, 8, 0, 0, tzinfo=timezone.utc),
            "via_channel": "email",
        }],
    )
    return email


@pytest.mark.asyncio
async def test_refresh_updates_status_and_appends_our_reply(adb):
    """After a chair reply, refresh pulls the authoritative 'solved' status and
    appends the chair's new comment (id 2) — while NOT duplicating comment 1."""
    email = await _seed_ticket_email(adb, ticket_id=777, requester_id=500, status="open")

    # Zendesk now reports the ticket solved, with the chair's reply (comment 2)
    # alongside the original requester comment (1).
    ticket = {"id": 777, "status": "solved", "requester_id": 500,
              "updated_at": "2026-07-16T10:00:00Z"}
    comments = {
        "comments": [
            _comment(1, 500, body="original question"),
            _comment(2, 900, body="Here is our reply. Best, Marc", created="2026-07-16T10:00:00Z"),
        ],
        "users": [_user(500, "end-user"), _user(900, "agent")],
    }
    client = RefreshFakeClient(ticket, comments)
    adapter = ZendeskIngestAdapter(provider=FakeProvider())

    out = await adapter.refresh_ticket(adb, 777, client=client, sleep=_nosleep)

    assert out == {"zendesk_status": "solved", "new_messages": 1}

    # Local row moved to the solved bucket (authoritative).
    refreshed = (
        await adb.execute(select(Email).where(Email.id == email.id))
    ).scalar_one()
    assert refreshed.zendesk_status == "solved"
    assert refreshed.last_processed_comment_id == 2

    # Thread now has both comments; the chair's reply (author 900 != requester 500)
    # is present and comment 1 was not duplicated.
    rows = (
        await adb.execute(
            select(EmailThreadMessage).where(EmailThreadMessage.email_id == email.id)
        )
    ).scalars().all()
    assert {r.zendesk_comment_id for r in rows} == {1, 2}
    reply = next(r for r in rows if r.zendesk_comment_id == 2)
    assert reply.author_id == 900
    assert "our reply" in reply.plain_body


@pytest.mark.asyncio
async def test_refresh_no_new_comments_is_noop_on_thread(adb):
    """If Zendesk returns only comments we already have, nothing is appended but
    the status is still reconciled."""
    email = await _seed_ticket_email(adb, ticket_id=778, requester_id=500, status="open")
    ticket = {"id": 778, "status": "pending", "requester_id": 500,
              "updated_at": "2026-07-16T10:00:00Z"}
    comments = {"comments": [_comment(1, 500)], "users": [_user(500, "end-user")]}
    adapter = ZendeskIngestAdapter(provider=FakeProvider())

    out = await adapter.refresh_ticket(
        adb, 778, client=RefreshFakeClient(ticket, comments), sleep=_nosleep
    )

    assert out == {"zendesk_status": "pending", "new_messages": 0}
    refreshed = (
        await adb.execute(select(Email).where(Email.id == email.id))
    ).scalar_one()
    assert refreshed.zendesk_status == "pending"
    rows = (
        await adb.execute(
            select(EmailThreadMessage).where(EmailThreadMessage.email_id == email.id)
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_refresh_unknown_ticket_returns_none(adb):
    """No local row maps to the ticket id → returns None, no HTTP performed."""
    client = RefreshFakeClient({"id": 999, "status": "open"}, {"comments": [], "users": []})
    adapter = ZendeskIngestAdapter(provider=FakeProvider())

    out = await adapter.refresh_ticket(adb, 999, client=client, sleep=_nosleep)

    assert out is None
    assert client.get_calls == []  # short-circuits before any read
