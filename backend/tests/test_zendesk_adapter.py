"""Tests for the Zendesk read/ingest adapter (Piece 4).

Fully hermetic — NOTHING hits real Zendesk. All HTTP goes through a fake async
client returning canned incremental-export / comments payloads; credentials are
a stub provider; the pipeline is a fake that creates a row without invoking the
real classifier/retriever/drafter. The DB is an in-memory async SQLite built
with ``Base.metadata.create_all``.
"""

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.v1 import zendesk as zendesk_api
from app.db.database import Base
from app.db.models import Email, EmailThreadMessage
from app.integrations.zendesk import adapter as adapter_mod
from app.integrations.zendesk.adapter import (
    SyncResult,
    ZendeskIngestAdapter,
    run_sync_cycle,
    zendesk_poll_loop,
)
from app.models.enums import EmailSource, EmailStatus, MessageAuthorRole
from app.repositories.email_repository import EmailRepository


# --- test doubles ----------------------------------------------------------


async def _nosleep(*_args, **_kwargs):
    return None


class FakeProvider:
    base_url = "https://aaai.zendesk.com/api/v2"

    def get_auth_header(self):
        return {"Authorization": "Bearer test-token"}


class FakePipeline:
    """Stands in for EmailPipeline: creates a classified row, no real modules.

    ``process_email`` (initial inquiry) and ``_compute`` (per-message follow-up
    core, Piece T2) both record their calls so tests can assert exactly which
    path ran. ``_compute`` returns a ``_Computed``-shaped object exposing the
    ``.record`` dict the orchestrator builds, with values distinct from the
    initial-inquiry canned output so a follow-up result is distinguishable from
    the parent Email's own.
    """

    def __init__(self, fail_bodies=None):
        self.calls: list[dict] = []
        self.compute_calls: list[dict] = []
        # Follow-up bodies whose compute() should raise (failure-injection for
        # per-message isolation tests).
        self._fail_bodies = set(fail_bodies or ())
        self._repo = EmailRepository()

    async def process_email(self, email_data, db):
        self.calls.append(email_data)
        email = await self._repo.create_email(
            db,
            {
                "sender": email_data.get("from") or "x@x",
                "sender_name": email_data.get("sender_name"),
                "subject": email_data.get("subject") or "",
                "body": email_data.get("body") or "",
                "status": EmailStatus.DRAFT_GENERATED.value,
                "classification": {"intent": "cms_support", "confidence": 0.9},
                "routing": {"lane": "human_review"},
                "draft": {"draft_text": "draft"},
            },
        )
        return SimpleNamespace(email_id=email.id)

    async def compute(self, email_data, db):
        self.compute_calls.append(email_data)
        if email_data.get("body") in self._fail_bodies:
            raise RuntimeError("simulated drafter/retriever failure")
        record = {
            "classification": {"intent": "author_list_change", "confidence": 0.77},
            "routing": {"lane": "human_review"},
            "draft": {"draft_text": f"followup draft: {email_data.get('body')}"},
            "retrieval_context": {
                "query": email_data.get("body"),
                "intent": "",
                "retrieved_ids": ["policy_101"],
            },
        }
        return SimpleNamespace(record=record)


class FakeResponse:
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://aaai.zendesk.com/x")
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=request,
                response=httpx.Response(self.status_code, request=request),
            )

    def json(self):
        return self._payload


class FakeAsyncClient:
    """Routes GETs to canned incremental pages / per-ticket comment payloads."""

    def __init__(self, incremental_pages, comments_by_ticket, bad_status=None):
        self.incremental_pages = list(incremental_pages)
        self.comments_by_ticket = comments_by_ticket
        self.bad_status = bad_status or {}  # ticket_id -> status code for comments
        self.get_calls: list[tuple[str, dict]] = []

    async def get(self, url, params=None, headers=None):
        self.get_calls.append((url, params or {}))
        if "incremental/tickets/cursor" in url:
            return FakeResponse(200, self.incremental_pages.pop(0))
        if "/comments" in url:
            tid = int(url.split("/tickets/")[1].split("/comments")[0])
            if tid in self.bad_status:
                return FakeResponse(self.bad_status[tid], {})
            return FakeResponse(
                200, self.comments_by_ticket.get(tid, {"comments": [], "users": []})
            )
        return FakeResponse(404, {})

    async def aclose(self):
        return None


# --- payload builders ------------------------------------------------------


def _user(uid, role, name=None, email=None):
    return {"id": uid, "role": role, "name": name or f"user{uid}", "email": email or f"u{uid}@ex.org"}


def _ticket(tid, *, status="open", requester_id=500, subject="Subj", updated="2026-07-15T09:00:00Z"):
    return {
        "id": tid,
        "status": status,
        "requester_id": requester_id,
        "subject": subject,
        "created_at": "2026-07-15T08:00:00Z",
        "updated_at": updated,
    }


def _comment(cid, author_id, *, public=True, body="text", created="2026-07-15T09:00:00Z"):
    return {
        "id": cid,
        "public": public,
        "author_id": author_id,
        "plain_body": body,
        "html_body": f"<p>{body}</p>",
        "created_at": created,
        "via": {"channel": "email"},
    }


def _incremental_page(tickets, *, users, cursor="CURSOR1", end=True):
    return {"tickets": tickets, "users": users, "after_cursor": cursor, "end_of_stream": end}


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


def _adapter():
    return ZendeskIngestAdapter(provider=FakeProvider(), pipeline=FakePipeline())


# === core polling behavior =================================================


@pytest.mark.asyncio
async def test_cursor_persists_and_resumes(adb):
    requester = _user(500, "end-user")
    page = _incremental_page([_ticket(100)], users=[requester], cursor="CUR_A")
    comments = {100: {"comments": [_comment(9001, 500)], "users": [requester]}}

    adapter = _adapter()
    await adapter.sync(adb, client=FakeAsyncClient([page], comments), sleep=_nosleep)

    from app.db.models import ZendeskSyncState
    state = (await adb.execute(select(ZendeskSyncState))).scalar_one()
    assert state.cursor == "CUR_A"

    # Second cycle must resume FROM the stored cursor, not start_time.
    page2 = _incremental_page([], users=[], cursor="CUR_B")
    client2 = FakeAsyncClient([page2], {})
    await _adapter().sync(adb, client=client2, sleep=_nosleep)
    incr_call = next(c for c in client2.get_calls if "incremental" in c[0])
    assert incr_call[1].get("cursor") == "CUR_A"
    assert "start_time" not in incr_call[1]


@pytest.mark.asyncio
async def test_upsert_creates_then_updates_without_reclassifying(adb):
    requester = _user(500, "end-user")
    comments = {100: {"comments": [_comment(9001, 500)], "users": [requester]}}
    pipeline = FakePipeline()
    adapter = ZendeskIngestAdapter(provider=FakeProvider(), pipeline=pipeline)

    # First poll: unseen ticket -> create + classify once.
    page1 = _incremental_page([_ticket(100, status="open")], users=[requester])
    res1 = await adapter.sync(adb, client=FakeAsyncClient([page1], comments), sleep=_nosleep)
    assert (res1.created, res1.updated, res1.classified) == (1, 0, 1)
    assert len(pipeline.calls) == 1

    emails = (await adb.execute(select(Email))).scalars().all()
    assert len(emails) == 1
    assert emails[0].zendesk_ticket_id == 100
    assert emails[0].source == EmailSource.ZENDESK.value

    # Second poll: same ticket, new status -> update, NO new row, NO reclassify.
    page2 = _incremental_page(
        [_ticket(100, status="solved", updated="2026-07-15T10:00:00Z")], users=[requester]
    )
    res2 = await ZendeskIngestAdapter(provider=FakeProvider(), pipeline=pipeline).sync(
        adb, client=FakeAsyncClient([page2], comments), sleep=_nosleep
    )
    assert (res2.created, res2.updated, res2.classified) == (0, 1, 0)
    assert len(pipeline.calls) == 1  # still one — never reclassified
    emails = (await adb.execute(select(Email))).scalars().all()
    assert len(emails) == 1
    assert emails[0].zendesk_status == "solved"


@pytest.mark.asyncio
async def test_new_customer_reply_surfaced_without_redraft(adb):
    """Follow-up policy (a): a NEW public end-user comment on an already-processed
    ticket is surfaced via an audit signal — the parent Email is never
    reclassified/redrafted (``process_email``/``reprocess_email`` untouched).
    Piece T2 additionally stores a per-message result; that is asserted in
    ``test_new_customer_followup_creates_processing_result``.
    """
    from app.db.models import AuditLog

    requester = _user(500, "end-user")
    pipeline = FakePipeline()
    adapter = ZendeskIngestAdapter(provider=FakeProvider(), pipeline=pipeline)

    # First poll: initial inquiry -> create + classify once.
    page1 = _incremental_page([_ticket(100, status="open")], users=[requester])
    comments1 = {100: {"comments": [_comment(9001, 500)], "users": [requester]}}
    await adapter.sync(adb, client=FakeAsyncClient([page1], comments1), sleep=_nosleep)

    # Second poll: same ticket, plus a NEW public end-user comment (9002).
    page2 = _incremental_page(
        [_ticket(100, status="open", updated="2026-07-15T11:00:00Z")], users=[requester]
    )
    comments2 = {
        100: {
            "comments": [
                _comment(9001, 500),
                _comment(9002, 500, body="Any update on this?", created="2026-07-15T11:00:00Z"),
            ],
            "users": [requester],
        }
    }
    res2 = await ZendeskIngestAdapter(provider=FakeProvider(), pipeline=pipeline).sync(
        adb, client=FakeAsyncClient([page2], comments2), sleep=_nosleep
    )

    # Surfaced as a customer reply; parent NOT reclassified/redrafted.
    assert res2.customer_replies == 1
    assert res2.classified == 0
    assert len(pipeline.calls) == 1  # process_email ran once (initial inquiry only)

    audits = (
        await adb.execute(
            select(AuditLog).where(AuditLog.action == "customer_reply_received")
        )
    ).scalars().all()
    assert len(audits) == 1
    assert 9002 in audits[0].extra_metadata["comment_ids"]


@pytest.mark.asyncio
async def test_new_customer_followup_creates_processing_result(adb):
    """Piece T2: a new requester follow-up gets its OWN EmailProcessingResult
    with populated classification/routing/draft/retrieval_context, linked to the
    real persisted thread-message id — and the parent Email is left untouched.
    """
    from app.db.models import EmailProcessingResult, EmailThreadMessage

    requester = _user(500, "end-user")
    pipeline = FakePipeline()
    adapter = ZendeskIngestAdapter(provider=FakeProvider(), pipeline=pipeline)

    # First poll: initial inquiry -> create + classify once (parent Email).
    page1 = _incremental_page([_ticket(100, status="open")], users=[requester])
    comments1 = {100: {"comments": [_comment(9001, 500)], "users": [requester]}}
    await adapter.sync(adb, client=FakeAsyncClient([page1], comments1), sleep=_nosleep)

    parent = (await adb.execute(select(Email))).scalar_one()
    parent_id = parent.id
    parent_subject = parent.subject
    parent_classification_before = dict(parent.classification)

    # Second poll: same ticket + a NEW public end-user comment (9002).
    page2 = _incremental_page(
        [_ticket(100, status="open", updated="2026-07-15T11:00:00Z")], users=[requester]
    )
    comments2 = {
        100: {
            "comments": [
                _comment(9001, 500),
                _comment(9002, 500, body="Any update on this?", created="2026-07-15T11:00:00Z"),
            ],
            "users": [requester],
        }
    }
    await ZendeskIngestAdapter(provider=FakeProvider(), pipeline=pipeline).sync(
        adb, client=FakeAsyncClient([page2], comments2), sleep=_nosleep
    )

    # _compute ran exactly once, for the follow-up body (not the initial inquiry).
    assert len(pipeline.compute_calls) == 1
    assert pipeline.compute_calls[0]["body"] == "Any update on this?"
    # Subject came from the parent ticket.
    assert pipeline.compute_calls[0]["subject"] == parent_subject

    # Exactly one EmailProcessingResult, linked to the follow-up message (9002).
    results = (await adb.execute(select(EmailProcessingResult))).scalars().all()
    assert len(results) == 1
    result = results[0]
    followup_msg = (
        await adb.execute(
            select(EmailThreadMessage).where(
                EmailThreadMessage.zendesk_comment_id == 9002
            )
        )
    ).scalar_one()
    assert result.thread_message_id == followup_msg.id

    # Columns populated from the compute record (incl. denormalized lane/confidence).
    assert result.classification == {"intent": "author_list_change", "confidence": 0.77}
    assert result.routing == {"lane": "human_review"}
    assert result.draft["draft_text"] == "followup draft: Any update on this?"
    assert result.retrieval_context["retrieved_ids"] == ["policy_101"]
    assert result.lane == "human_review"
    assert result.confidence == 0.77

    # Parent Email's OWN pipeline output is unchanged (re-fetch fresh from DB).
    parent_after = (
        await adb.execute(select(Email).where(Email.id == parent_id))
    ).scalar_one()
    assert parent_after.classification == parent_classification_before
    assert parent_after.classification["confidence"] == 0.9  # initial-inquiry value


@pytest.mark.asyncio
async def test_followup_failure_isolated_within_sync_batch(adb):
    """T2c: one follow-up whose pipeline run fails does NOT abort the batch —
    a follow-up on another ticket still processes, and the failure is counted on
    ``failed_processing`` + recorded in ``errors`` (observable to a chair later).
    """
    from app.db.models import EmailProcessingResult

    requester = _user(500, "end-user")
    # compute() raises for the ticket-100 follow-up body; succeeds otherwise.
    pipeline = FakePipeline(fail_bodies={"boom please fail"})

    # Poll 1: create two tickets (100, 200), each with an initial inquiry.
    page1 = _incremental_page(
        [_ticket(100, status="open"), _ticket(200, status="open")], users=[requester]
    )
    comments1 = {
        100: {"comments": [_comment(9001, 500)], "users": [requester]},
        200: {"comments": [_comment(8001, 500)], "users": [requester]},
    }
    await ZendeskIngestAdapter(provider=FakeProvider(), pipeline=pipeline).sync(
        adb, client=FakeAsyncClient([page1], comments1), sleep=_nosleep
    )

    # Poll 2: ticket 100 gets a BAD follow-up; ticket 200 gets a GOOD follow-up.
    page2 = _incremental_page(
        [
            _ticket(100, status="open", updated="2026-07-15T11:00:00Z"),
            _ticket(200, status="open", updated="2026-07-15T11:00:00Z"),
        ],
        users=[requester],
    )
    comments2 = {
        100: {
            "comments": [
                _comment(9001, 500),
                _comment(9002, 500, body="boom please fail", created="2026-07-15T11:00:00Z"),
            ],
            "users": [requester],
        },
        200: {
            "comments": [
                _comment(8001, 500),
                _comment(8002, 500, body="a normal question", created="2026-07-15T11:00:00Z"),
            ],
            "users": [requester],
        },
    }
    res2 = await ZendeskIngestAdapter(provider=FakeProvider(), pipeline=pipeline).sync(
        adb, client=FakeAsyncClient([page2], comments2), sleep=_nosleep
    )

    # Batch completed; both replies surfaced; exactly one follow-up failed.
    assert res2.customer_replies == 2
    assert res2.failed_processing == 1
    assert len(res2.errors) == 1
    assert "ticket 100" in res2.errors[0] and "9002" in res2.errors[0]
    assert res2.failed == 0  # ticket-level failure is a different counter

    # Only the GOOD follow-up (ticket 200) produced a result row.
    results = (await adb.execute(select(EmailProcessingResult))).scalars().all()
    assert len(results) == 1
    assert results[0].draft["draft_text"] == "followup draft: a normal question"


@pytest.mark.asyncio
async def test_chair_and_internal_followups_do_not_create_processing_result(adb):
    """Only requester (public end-user) follow-ups trigger a result: a chair
    (agent) public reply and a non-public end-user internal note do NOT.
    """
    from app.db.models import EmailProcessingResult

    requester = _user(500, "end-user")
    agent = _user(700, "agent")
    pipeline = FakePipeline()
    adapter = ZendeskIngestAdapter(provider=FakeProvider(), pipeline=pipeline)

    # First poll: initial inquiry.
    page1 = _incremental_page([_ticket(100, status="open")], users=[requester])
    comments1 = {100: {"comments": [_comment(9001, 500)], "users": [requester]}}
    await adapter.sync(adb, client=FakeAsyncClient([page1], comments1), sleep=_nosleep)

    # Second poll: a chair (agent) public reply + a non-public end-user note —
    # neither is a requester follow-up.
    page2 = _incremental_page(
        [_ticket(100, status="open", updated="2026-07-15T11:00:00Z")], users=[requester, agent]
    )
    comments2 = {
        100: {
            "comments": [
                _comment(9001, 500),
                _comment(9002, 700, body="Chair replying here", created="2026-07-15T11:00:00Z"),
                _comment(9003, 500, public=False, body="private note", created="2026-07-15T11:30:00Z"),
            ],
            "users": [requester, agent],
        }
    }
    res2 = await ZendeskIngestAdapter(provider=FakeProvider(), pipeline=pipeline).sync(
        adb, client=FakeAsyncClient([page2], comments2), sleep=_nosleep
    )

    # No requester follow-up → no customer-reply signal, no _compute, no result.
    assert res2.customer_replies == 0
    assert len(pipeline.compute_calls) == 0
    results = (await adb.execute(select(EmailProcessingResult))).scalars().all()
    assert results == []


@pytest.mark.asyncio
async def test_add_thread_messages_returns_persisted_rows(adb):
    """The changed add_thread_messages contract: returns the persisted rows with
    populated ids (not a count), and [] when there is nothing to add.
    """
    repo = EmailRepository()
    users = {500: {"role": "end-user", "name": "u", "email": "u@x"}}
    project = ZendeskIngestAdapter(provider=FakeProvider())._to_message_dict
    email = await repo.create_email(
        adb,
        {"sender": "a@b.c", "subject": "s", "body": "b",
         "source": EmailSource.ZENDESK.value, "zendesk_ticket_id": 4242},
    )
    rows = await repo.add_thread_messages(
        adb,
        str(email.id),
        [project(_comment(1, 500), users), project(_comment(2, 500), users)],
    )
    assert len(rows) == 2
    assert all(isinstance(r, EmailThreadMessage) and r.id is not None for r in rows)
    assert {r.zendesk_comment_id for r in rows} == {1, 2}

    # Nothing to add → empty list (not an error, not a count).
    assert await repo.add_thread_messages(adb, str(email.id), []) == []


@pytest.mark.asyncio
async def test_deleted_tickets_filtered_out(adb):
    requester = _user(500, "end-user")
    page = _incremental_page(
        [_ticket(100, status="deleted"), _ticket(101, status="open")],
        users=[requester],
    )
    comments = {101: {"comments": [_comment(9001, 500)], "users": [requester]}}
    res = await _adapter().sync(adb, client=FakeAsyncClient([page], comments), sleep=_nosleep)

    assert res.skipped_deleted == 1
    assert res.created == 1
    emails = (await adb.execute(select(Email))).scalars().all()
    assert [e.zendesk_ticket_id for e in emails] == [101]


# === status allow-list filtering (config default + per-call override) ======


@pytest.mark.asyncio
async def test_sync_without_statuses_uses_config_default(adb, monkeypatch):
    """No per-call override → the ZENDESK_SYNC_STATUSES config default applies."""
    # Restrict the configured allow-list to a subset for this test.
    monkeypatch.setattr(adapter_mod.settings, "ZENDESK_SYNC_STATUSES", "open,pending")

    requester = _user(500, "end-user")
    page = _incremental_page(
        [_ticket(100, status="open"), _ticket(101, status="closed")],
        users=[requester],
    )
    comments = {
        100: {"comments": [_comment(9001, 500)], "users": [requester]},
        101: {"comments": [_comment(9002, 500)], "users": [requester]},
    }
    # sync() called WITHOUT statuses → config default (open,pending) is used.
    res = await _adapter().sync(
        adb, client=FakeAsyncClient([page], comments), sleep=_nosleep
    )

    assert res.created == 1
    assert res.skipped_status == 1
    emails = (await adb.execute(select(Email))).scalars().all()
    assert [e.zendesk_ticket_id for e in emails] == [100]


@pytest.mark.asyncio
async def test_statuses_param_overrides_config_for_that_call_only(adb):
    """An explicit statuses list overrides config for THAT call; the next call
    without it reverts to the (default = all) config, so the override is not
    sticky."""
    requester = _user(500, "end-user")
    comments = {
        100: {"comments": [_comment(9001, 500)], "users": [requester]},
        101: {"comments": [_comment(9002, 500)], "users": [requester]},
    }

    # Cycle 1: override to open-only. The solved ticket (101) is filtered out.
    page1 = _incremental_page(
        [_ticket(100, status="open"), _ticket(101, status="solved")],
        users=[requester],
        cursor="CUR_A",
    )
    res1 = await _adapter().sync(
        adb,
        client=FakeAsyncClient([page1], comments),
        statuses=["open"],
        sleep=_nosleep,
    )
    assert res1.created == 1
    assert res1.skipped_status == 1
    assert [e.zendesk_ticket_id for e in (await adb.execute(select(Email))).scalars().all()] == [100]

    # Cycle 2: SAME solved ticket, NO override → config default (all statuses)
    # applies, so 101 is now ingested. Proves the override was per-call only.
    page2 = _incremental_page(
        [_ticket(101, status="solved", updated="2026-07-15T12:00:00Z")],
        users=[requester],
        cursor="CUR_B",
    )
    res2 = await _adapter().sync(
        adb, client=FakeAsyncClient([page2], comments), sleep=_nosleep
    )
    assert res2.created == 1
    assert res2.skipped_status == 0
    assert sorted(
        e.zendesk_ticket_id for e in (await adb.execute(select(Email))).scalars().all()
    ) == [100, 101]


@pytest.mark.asyncio
async def test_endpoint_parses_and_threads_statuses(monkeypatch):
    """The endpoint parses the raw param with the shared helper (normalize +
    validate) and threads the result to run_sync_cycle; omitting it passes None."""
    captured = {}

    async def spy(db, **kwargs):
        captured.update(kwargs)
        return SyncResult(created=1)

    monkeypatch.setattr(zendesk_api, "run_sync_cycle", spy)

    # Provided (messy input): normalized/validated, unknown token dropped, deduped.
    await zendesk_api.sync_zendesk(db=object(), statuses="Open, pending ,open,bogus")
    assert captured["statuses"] == ["open", "pending"]

    # Omitted → None, so the adapter uses the config default.
    captured.clear()
    await zendesk_api.sync_zendesk(db=object(), statuses=None)
    assert captured["statuses"] is None


@pytest.mark.asyncio
async def test_endpoint_invalid_statuses_falls_back_to_all(monkeypatch):
    """An all-invalid/blank override falls back to every valid status — the same
    safe behavior as the config property, not an empty (ingest-nothing) list."""
    from app.core.config import Settings

    captured = {}

    async def spy(db, **kwargs):
        captured.update(kwargs)
        return SyncResult()

    monkeypatch.setattr(zendesk_api, "run_sync_cycle", spy)
    await zendesk_api.sync_zendesk(db=object(), statuses="bogus, , xxx")
    assert captured["statuses"] == sorted(Settings.ZENDESK_VALID_STATUSES)


@pytest.mark.asyncio
async def test_poll_loop_uses_config_default_no_statuses(monkeypatch):
    """The background loop never passes a statuses override — it always uses the
    config default (unaffected by the per-call endpoint param)."""
    import asyncio

    stop = asyncio.Event()
    captured = []

    async def spy(db, **kwargs):
        captured.append(kwargs)
        stop.set()
        return SyncResult()

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(adapter_mod, "run_sync_cycle", spy)
    await zendesk_poll_loop(stop, interval=999, session_factory=_Factory(), sleep=_nosleep)
    assert len(captured) == 1
    # The loop passes no statuses kwarg at all → config default governs.
    assert "statuses" not in captured[0]


@pytest.mark.asyncio
async def test_comments_become_thread_messages(adb):
    requester = _user(500, "end-user")
    agent = _user(600, "agent")
    comments = {
        100: {
            "comments": [
                _comment(9001, 500, public=True, body="the question", created="2026-07-15T09:00:00Z"),
                _comment(9002, 600, public=False, body="internal note", created="2026-07-15T09:30:00Z"),
                _comment(9003, 600, public=True, body="the reply", created="2026-07-15T10:00:00Z"),
            ],
            "users": [requester, agent],
        }
    }
    page = _incremental_page([_ticket(100)], users=[requester])
    await _adapter().sync(adb, client=FakeAsyncClient([page], comments), sleep=_nosleep)

    msgs = (
        await adb.execute(
            select(EmailThreadMessage).order_by(EmailThreadMessage.created_at)
        )
    ).scalars().all()
    assert len(msgs) == 3
    assert [m.zendesk_comment_id for m in msgs] == [9001, 9002, 9003]
    assert [m.public for m in msgs] == [True, False, True]
    assert msgs[0].author_role == MessageAuthorRole.END_USER.value
    assert msgs[1].author_role == MessageAuthorRole.AGENT.value
    assert msgs[1].plain_body == "internal note"


@pytest.mark.asyncio
async def test_initial_inquiry_is_classified_message(adb):
    """The classified body is the first public end-user comment, not an agent's."""
    requester = _user(500, "end-user")
    agent = _user(600, "agent")
    comments = {
        100: {
            "comments": [
                _comment(9001, 600, public=True, body="agent auto-ack", created="2026-07-15T08:30:00Z"),
                _comment(9002, 500, public=True, body="my real question", created="2026-07-15T09:00:00Z"),
            ],
            "users": [requester, agent],
        }
    }
    page = _incremental_page([_ticket(100)], users=[requester])
    pipeline = FakePipeline()
    await ZendeskIngestAdapter(provider=FakeProvider(), pipeline=pipeline).sync(
        adb, client=FakeAsyncClient([page], comments), sleep=_nosleep
    )
    assert len(pipeline.calls) == 1
    assert pipeline.calls[0]["body"] == "my real question"


@pytest.mark.asyncio
async def test_single_ticket_failure_does_not_halt_batch(adb):
    requester = _user(500, "end-user")
    comments = {
        100: {"comments": [_comment(9001, 500)], "users": [requester]},
        # ticket 101: malformed created_at -> parsing raises during processing
        101: {"comments": [_comment(9002, 500, created="not-a-real-date")], "users": [requester]},
        102: {"comments": [_comment(9003, 500)], "users": [requester]},
    }
    page = _incremental_page(
        [_ticket(100), _ticket(101), _ticket(102)], users=[requester]
    )
    res = await _adapter().sync(adb, client=FakeAsyncClient([page], comments), sleep=_nosleep)

    assert res.failed == 1
    assert res.created == 2
    assert len(res.errors) == 1 and "101" in res.errors[0]
    emails = (await adb.execute(select(Email))).scalars().all()
    assert sorted(e.zendesk_ticket_id for e in emails) == [100, 102]


@pytest.mark.asyncio
async def test_comment_fetch_error_isolated_to_one_ticket(adb):
    """A transient (non-retryable) HTTP error on one ticket's comments is isolated."""
    requester = _user(500, "end-user")
    comments = {100: {"comments": [_comment(9001, 500)], "users": [requester]}}
    page = _incremental_page([_ticket(100), _ticket(101)], users=[requester])
    client = FakeAsyncClient([page], comments, bad_status={101: 404})
    res = await _adapter().sync(adb, client=client, sleep=_nosleep)
    assert res.created == 1
    assert res.failed == 1


# === shared-function wiring (manual endpoint + loop use ONE function) ======


@pytest.mark.asyncio
async def test_run_sync_cycle_delegates_to_adapter(adb, monkeypatch):
    seen = {}

    async def spy(self, db, *, client=None, max_pages=None, per_page=None, statuses=None):
        seen["called"] = True
        return SyncResult(pages=1)

    monkeypatch.setattr(ZendeskIngestAdapter, "sync", spy)
    result = await run_sync_cycle(adb)
    assert seen.get("called") is True
    assert result.pages == 1


@pytest.mark.asyncio
async def test_manual_endpoint_calls_run_sync_cycle(monkeypatch):
    calls = []

    async def spy(db, **kwargs):
        calls.append(db)
        return SyncResult(created=3)

    monkeypatch.setattr(zendesk_api, "run_sync_cycle", spy)
    sentinel_db = object()
    result = await zendesk_api.sync_zendesk(db=sentinel_db)
    assert calls == [sentinel_db]
    assert result["created"] == 3


@pytest.mark.asyncio
async def test_poll_loop_calls_run_sync_cycle_each_iteration(monkeypatch):
    import asyncio

    stop = asyncio.Event()
    calls = []

    async def spy(db, **kwargs):
        calls.append(1)
        stop.set()  # stop after one cycle
        return SyncResult()

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(adapter_mod, "run_sync_cycle", spy)
    await zendesk_poll_loop(stop, interval=999, session_factory=_Factory(), sleep=_nosleep)
    assert len(calls) == 1
