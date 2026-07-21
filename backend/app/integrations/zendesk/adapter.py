"""Zendesk read/ingest adapter (Piece 4) — the live poller.

Pulls tickets from Zendesk via the incremental cursor export (ZENDESK_API.md §2)
into our schema and feeds each genuinely new inquiry through the existing
pipeline. READ-ONLY: this piece never writes back to Zendesk (that is Piece 5).

Design notes:
- A Zendesk ticket maps 1:1 onto an ``Email`` row, deduped by the unique
  ``zendesk_ticket_id`` (§10). Because the orchestrator's ``process_email``
  always CREATES a row (and must not be modified), this adapter owns dedup: it
  lets the pipeline create the row for a brand-new ticket's initial inquiry,
  then decorates that row with the Zendesk fields; for a ticket we've already
  seen it appends new thread messages and updates status WITHOUT re-running the
  pipeline. So classification happens exactly once per ticket — on the initial
  inquiry — never on every poll.
- The cursor is checkpointed to the DB after every page, so a restart resumes.
- The same ``run_sync_cycle`` function backs BOTH the manual endpoint and the
  background loop; adding a webhook trigger later is just another caller.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.database import async_session_factory
from app.integrations.zendesk.credential_provider import (
    ZendeskCredentialProvider,
    get_zendesk_credential_provider,
)
from app.models.enums import EmailSource, EmailStatus, MessageAuthorRole
from app.pipeline.orchestrator import EmailPipeline
from app.repositories.audit_repository import AuditRepository
from app.repositories.email_repository import EmailRepository
from app.repositories.zendesk_repository import ZendeskSyncStateRepository

logger = logging.getLogger(__name__)

INCREMENTAL_PATH = "/incremental/tickets/cursor.json"
COMMENTS_PATH = "/tickets/{ticket_id}/comments.json"

# Incremental export allows 10 req/min — stay just under it between pages (same
# slack the one-off pull script uses). Comment fetches use a separate, larger
# budget, so only a token pause between them.
PAGE_SLEEP_SECONDS = 6.5
COMMENT_SLEEP_SECONDS = 0.2
# Transient-failure retry budget for a single HTTP call.
MAX_HTTP_ATTEMPTS = 6
DEFAULT_RETRY_AFTER_SECONDS = 30
# Overlap guard: a claimed sync whose lock hasn't refreshed in this long is
# assumed crashed, so a new cycle may reclaim it (well above a normal cycle's
# duration — 10 req/min pacing means even a large cycle finishes well inside it).
SYNC_LOCK_STALE_SECONDS = 900


class ZendeskAdapterError(RuntimeError):
    """Raised when a Zendesk read call fails unrecoverably (after retries)."""


class SyncResult(BaseModel):
    """Per-cycle outcome — the count of what happened, surfaced to callers."""

    pages: int = 0
    tickets_seen: int = 0
    created: int = 0
    updated: int = 0
    skipped_deleted: int = 0
    # Tickets skipped because their status is not in the active allow-list
    # (ZENDESK_SYNC_STATUSES config default, or a per-call override).
    skipped_status: int = 0
    classified: int = 0
    # New public end-user replies on already-processed tickets (surfaced for a
    # chair to review; never auto-redrafted — see the follow-up policy below).
    customer_replies: int = 0
    # Follow-ups that re-ran the full pipeline (new public end-user comment on a
    # non-closed ticket). Distinct from customer_replies (surface-only, closed).
    # This is the ACTIVE follow-up path (re-draft over the thread + draft.history).
    reprocessed: int = 0
    # DORMANT — retained for a later EmailProcessingResult integration. Would count
    # follow-up messages whose per-message compute+persist raised and was skipped.
    # The active path above is reprocess-on-thread, so this stays 0 for now.
    failed_processing: int = 0
    failed: int = 0
    # True when this trigger was refused because another cycle already holds the
    # single-flight lock (overlap guard) — no work was done.
    skipped: bool = False
    skipped_reason: str | None = None
    cursor: str | None = None
    errors: list[str] = Field(default_factory=list)


def _parse_dt(value: str | None) -> datetime | None:
    """Parse a Zendesk ISO-8601 timestamp (``...Z``) to an aware datetime."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _index_users(users: list[dict]) -> dict[int, dict]:
    """Map side-loaded user id -> {role, name, email} for author/requester joins."""
    return {
        u["id"]: {"role": u.get("role"), "name": u.get("name"), "email": u.get("email")}
        for u in users
        if u.get("id") is not None
    }


class ZendeskIngestAdapter:
    """Polls Zendesk and ingests tickets/comments into our schema (read-only)."""

    def __init__(
        self,
        *,
        provider: ZendeskCredentialProvider | None = None,
        pipeline: EmailPipeline | None = None,
        email_repo: EmailRepository | None = None,
        state_repo: ZendeskSyncStateRepository | None = None,
        audit_repo: AuditRepository | None = None,
    ) -> None:
        # Provider and pipeline are built lazily so constructing the adapter is
        # cheap and credential/pipeline setup only happens on a real sync.
        self._provider = provider
        self._pipeline = pipeline
        self.email_repo = email_repo or EmailRepository()
        self.state_repo = state_repo or ZendeskSyncStateRepository()
        self.audit_repo = audit_repo or AuditRepository()

    def _provider_obj(self) -> ZendeskCredentialProvider:
        if self._provider is None:
            self._provider = get_zendesk_credential_provider(settings)
        return self._provider

    def _pipeline_obj(self) -> EmailPipeline:
        if self._pipeline is None:
            self._pipeline = EmailPipeline()
        return self._pipeline

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict,
        sleep,
    ) -> dict:
        """GET with auth, 429 (Retry-After) and transient-5xx handling.

        The credential provider's ``get_auth_header`` is synchronous (it may do
        a blocking token refresh), so it runs in a thread to avoid stalling the
        event loop. Non-2xx that isn't 429/5xx raises (caught per-ticket upstream
        for comment fetches; fatal for the page fetch, which is correct).
        """
        provider = self._provider_obj()
        for attempt in range(MAX_HTTP_ATTEMPTS):
            headers = await asyncio.to_thread(provider.get_auth_header)
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", DEFAULT_RETRY_AFTER_SECONDS))
                logger.warning("Zendesk 429; sleeping %ss", wait)
                await sleep(wait)
                continue
            if resp.status_code >= 500:
                await sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        raise ZendeskAdapterError(f"GET {url} failed after {MAX_HTTP_ATTEMPTS} attempts")

    def _to_message_dict(self, comment: dict, users: dict[int, dict]) -> dict:
        """Project a Zendesk comment onto EmailThreadMessage fields."""
        author_id = comment.get("author_id")
        author = users.get(author_id, {})
        return {
            "zendesk_comment_id": comment.get("id"),
            "public": bool(comment.get("public")),
            "author_id": author_id,
            "author_role": author.get("role"),
            "plain_body": comment.get("plain_body") or comment.get("body"),
            "html_body": comment.get("html_body"),
            "created_at": _parse_dt(comment.get("created_at")),
            "via_channel": (comment.get("via") or {}).get("channel"),
        }

    @staticmethod
    def _find_initial_inquiry(messages: list[dict]) -> dict | None:
        """First public end-user message by created_at — the classified message."""
        ordered = sorted(
            (m for m in messages if m.get("created_at") is not None),
            key=lambda m: m["created_at"],
        )
        for m in ordered:
            if m["public"] and m.get("author_role") == MessageAuthorRole.END_USER.value:
                return m
        return None

    @staticmethod
    def _max_comment_id(messages: list[dict]) -> int | None:
        ids = [m["zendesk_comment_id"] for m in messages if m.get("zendesk_comment_id")]
        return max(ids) if ids else None

    async def _process_ticket(
        self,
        db: AsyncSession,
        client: httpx.AsyncClient,
        ticket: dict,
        users_map: dict[int, dict],
        result: SyncResult,
        sleep,
        allowed_statuses: set[str],
    ) -> None:
        """Upsert one ticket + its thread; classify only if new to us.

        ``allowed_statuses`` is the active status allow-list for this cycle
        (config default or per-call override). Tickets whose status is not in it
        are skipped before any comment fetch, so filtered statuses cost no
        request budget.
        """
        ticket_id = int(ticket["id"])
        ticket_status = ticket.get("status")
        if ticket_status == "deleted":
            result.skipped_deleted += 1
            return
        # Status allow-list filter (applied client-side — the incremental export
        # has no server-side status filter). "deleted" is handled above, so it is
        # never a valid allow-list value.
        if ticket_status not in allowed_statuses:
            result.skipped_status += 1
            return

        existing = await self.email_repo.get_by_zendesk_ticket_id(db, ticket_id)

        # Fetch the full thread for this ticket.
        base = self._provider_obj().base_url
        comments_data = await self._get(
            client,
            base + COMMENTS_PATH.format(ticket_id=ticket_id),
            {"include": "users", "sort": "created_at"},
            sleep,
        )
        await sleep(COMMENT_SLEEP_SECONDS)
        comment_users = _index_users(comments_data.get("users", []))
        messages = [
            self._to_message_dict(c, comment_users)
            for c in comments_data.get("comments", [])
        ]
        requester = (
            users_map.get(ticket.get("requester_id"))
            or comment_users.get(ticket.get("requester_id"))
            or {}
        )

        if existing is None:
            email_id = await self._ingest_new_ticket(
                db, ticket, requester, messages, result
            )
            await self.email_repo.apply_zendesk_fields(
                db,
                email_id,
                {
                    "source": EmailSource.ZENDESK.value,
                    "zendesk_ticket_id": ticket_id,
                    "zendesk_requester_id": ticket.get("requester_id"),
                    "zendesk_status": ticket_status,
                    "zendesk_created_at": _parse_dt(ticket.get("created_at")),
                    "zendesk_updated_at": _parse_dt(ticket.get("updated_at")),
                    "last_processed_comment_id": self._max_comment_id(messages),
                },
            )
            await self.email_repo.add_thread_messages(db, email_id, messages)
            result.created += 1
        else:
            email_id = str(existing.id)
            existing_ids = await self.email_repo.get_thread_comment_ids(db, email_id)
            new_messages = [
                m
                for m in messages
                if m.get("zendesk_comment_id") not in existing_ids
            ]
            added_rows = await self.email_repo.add_thread_messages(
                db, email_id, new_messages
            )
            await self.email_repo.apply_zendesk_fields(
                db,
                email_id,
                {
                    "zendesk_status": ticket_status,
                    "zendesk_updated_at": _parse_dt(ticket.get("updated_at")),
                    "last_processed_comment_id": (
                        self._max_comment_id(messages)
                        or existing.last_processed_comment_id
                    ),
                },
            )
            # No reclassification of the INITIAL inquiry: the parent Email's own
            # classification/routing/draft are never touched (§10). But a NEW
            # public end-user follow-up now gets its OWN classify→retrieve→route→
            # draft cycle stored as an EmailProcessingResult (Piece T2) — plus the
            # existing customer_reply_received audit signal (option (a)), kept
            # unchanged (additive).
            new_customer = [
                m
                for m in new_messages
                if m.get("public")
                and m.get("author_role") == MessageAuthorRole.END_USER.value
            ]
            if new_customer:
                if ticket_status == "closed":
                    # Immutable ticket: a genuine reply spawns a NEW ticket
                    # (ingested via the new-ticket path). Surface only.
                    await self._record_customer_reply(
                        db, email_id, ticket_id, new_customer
                    )
                    result.customer_replies += 1
                else:
                    # solved reopens to open; open/pending/hold stay actionable.
                    # ACTIVE follow-up path: re-run the full pipeline over the
                    # whole thread (re-draft on the Email + draft.history). Sahil's
                    # per-message EmailProcessingResult path
                    # (_process_followup_messages, using ``added_rows``) is retained
                    # below but DORMANT — not wired in until a later integration.
                    await self._reprocess_on_followup(
                        db, email_id, ticket_id, new_customer
                    )
                    result.reprocessed += 1
            result.updated += 1

    async def _process_followup_messages(
        self,
        db: AsyncSession,
        email,
        ticket_id: int,
        new_customer: list[dict],
        added_rows: list,
        result: SyncResult,
    ) -> None:
        """Run the pipeline for each new requester follow-up; store its result.

        Each new public end-user message gets its own classify→retrieve→route→
        draft cycle via the orchestrator's public ``compute`` seam, persisted as
        a NEW ``EmailProcessingResult`` linked to that message. The parent
        ``Email``'s own classification/routing/draft are left untouched; only
        requester (end-user) messages reach here, never chair (agent/admin)
        replies or internal notes.

        Per-message failure isolation (T2c): a compute/persist error (drafter
        API error, retriever timeout, …) is logged, counted on
        ``result.failed_processing`` with detail in ``result.errors``, and
        SKIPPED — it never aborts the remaining follow-ups, this ticket, or the
        rest of the sync batch. A failed message simply has no result row (same
        surface as "not yet processed", disambiguated by the counter/log). The
        session is rolled back first so a mid-commit failure can't poison
        subsequent work (the customer_reply_received audit already committed, so
        rollback cannot lose it).
        """
        # Map the just-persisted rows by comment id so each result links to the
        # message's REAL primary key.
        by_comment_id = {
            row.zendesk_comment_id: row
            for row in added_rows
            if row.zendesk_comment_id is not None
        }
        pipeline = self._pipeline_obj()
        for m in new_customer:
            comment_id = m.get("zendesk_comment_id")
            message_row = by_comment_id.get(comment_id)
            if message_row is None:
                continue
            email_data = {
                "from": email.sender,
                "sender_name": email.sender_name,
                # Subject from the parent ticket; body is THIS follow-up's text.
                "subject": email.subject,
                "body": m.get("plain_body") or "",
            }
            try:
                computed = await pipeline.compute(email_data, db)
                await self.email_repo.add_processing_result(
                    db, message_row.id, computed.record
                )
            except Exception as exc:  # noqa: BLE001 - one follow-up must not halt the batch
                await db.rollback()
                result.failed_processing += 1
                result.errors.append(
                    f"ticket {ticket_id} follow-up comment {comment_id}: {exc}"
                )
                logger.exception(
                    "Follow-up processing failed for ticket %s comment %s; skipping.",
                    ticket_id,
                    comment_id,
                )

    async def _record_customer_reply(
        self,
        db: AsyncSession,
        email_id: str,
        ticket_id: int,
        new_customer: list[dict],
    ) -> None:
        """Surface a new end-user reply on an already-processed ticket (option a).

        Writes an append-only ``customer_reply_received`` audit entry (the signal
        the UI/audit view reads to badge "new customer reply") and does nothing
        else — no reclassification, no new draft. Best-effort: a logging failure
        must never abort the sync cycle.
        """
        try:
            await self.audit_repo.log_action(
                db,
                email_id,
                "customer_reply_received",
                "zendesk_sync",
                {
                    "zendesk_ticket_id": ticket_id,
                    "comment_ids": [
                        m.get("zendesk_comment_id") for m in new_customer
                    ],
                    "note": "New public end-user reply after processing; awaiting "
                    "chair review (no automatic redraft).",
                },
            )
        except Exception:  # noqa: BLE001 - the signal is best-effort
            logger.warning(
                "Failed to record customer_reply_received for ticket %s.",
                ticket_id,
                exc_info=True,
            )

    async def _reprocess_on_followup(
        self,
        db: AsyncSession,
        email_id: str,
        ticket_id: int,
        new_customer: list[dict],
    ) -> None:
        """Re-run the full pipeline over the thread for a new requester reply.

        Re-classifies + re-drafts over the whole conversation (latest turn
        anchored) and preserves the prior draft in ``draft.history[]`` via the
        orchestrator. Writes a ``reprocessed_on_followup`` audit entry. Best-
        effort: a failure here must never abort the sync cycle — the new comment
        is already stored, so the chair still sees the reply.
        """
        comment_ids = [m.get("zendesk_comment_id") for m in new_customer]
        try:
            email = await self.email_repo.get_email_by_id(db, email_id)
            if email is None:
                return
            messages = await self.email_repo.get_thread_messages(db, email_id)
            await self._pipeline_obj().reprocess_email_with_thread(
                db, email, messages, triggering_comment_ids=comment_ids
            )
            await self.audit_repo.log_action(
                db,
                email_id,
                "reprocessed_on_followup",
                "zendesk_sync",
                {
                    "zendesk_ticket_id": ticket_id,
                    "comment_ids": comment_ids,
                    "note": "New public end-user reply → full pipeline re-run "
                    "over the conversation thread.",
                },
            )
        except Exception:  # noqa: BLE001 - one ticket must not abort the cycle
            logger.warning(
                "Follow-up reprocess failed for ticket %s.", ticket_id, exc_info=True
            )

    async def _ingest_new_ticket(
        self,
        db: AsyncSession,
        ticket: dict,
        requester: dict,
        messages: list[dict],
        result: SyncResult,
    ) -> str:
        """Create the Email row for a new ticket, classifying if an inquiry exists.

        If a public end-user inquiry exists, it runs the full pipeline (which
        creates + classifies + drafts the row). Otherwise a bare, unclassified
        row is created so the ticket is still tracked; it will be classified only
        once its initial inquiry appears would require a follow-up piece — for
        now such tickets stay pending (rare: threads normally open with the
        requester's message).
        """
        initial = self._find_initial_inquiry(messages)
        if initial is not None:
            email_data = {
                "from": requester.get("email") or f"ticket-{ticket['id']}@zendesk.local",
                "sender_name": requester.get("name"),
                "subject": ticket.get("subject") or "",
                "body": initial.get("plain_body") or "",
                "timestamp": ticket.get("created_at") or "",
            }
            pipeline_result = await self._pipeline_obj().process_email(email_data, db)
            result.classified += 1
            return str(pipeline_result.email_id)

        email = await self.email_repo.create_email(
            db,
            {
                "sender": requester.get("email") or f"ticket-{ticket['id']}@zendesk.local",
                "sender_name": requester.get("name"),
                "subject": ticket.get("subject") or "",
                "body": ticket.get("description") or "",
                "status": EmailStatus.PENDING.value,
                "source": EmailSource.ZENDESK.value,
            },
        )
        return str(email.id)

    async def sync(
        self,
        db: AsyncSession,
        *,
        client: httpx.AsyncClient | None = None,
        max_pages: int | None = None,
        per_page: int | None = None,
        statuses: list[str] | None = None,
        sleep=asyncio.sleep,
    ) -> SyncResult:
        """Run ONE polling cycle: page the incremental export, upsert tickets.

        A single ticket's failure (bad data / transient error) is logged and
        counted, then skipped — it never aborts the cycle. Returns a
        :class:`SyncResult` with the per-cycle counts.

        ``statuses`` optionally overrides the ZENDESK_SYNC_STATUSES config
        allow-list for THIS call only (already parsed/validated by the caller);
        when ``None`` the configured default is used, so polling and
        unparameterized calls are unaffected.
        """
        # Resolve the active status allow-list once per cycle: a per-call override
        # if provided, else the config default. The config property always yields
        # a non-empty validated list, and the endpoint passes an already-parsed
        # list, so this set is always the effective, validated allow-list.
        allowed_statuses = set(
            statuses if statuses is not None else settings.zendesk_sync_statuses
        )
        subdomain = settings.ZENDESK_SUBDOMAIN or ""
        state = await self.state_repo.get_or_create(
            db, subdomain, settings.ZENDESK_SYNC_START_TIME
        )
        # Snapshot fields we need before acquiring the lock: the reject path
        # rolls back (releasing the row lock), which expires `state`, so reading
        # its attributes afterward would trigger a lazy reload.
        initial_cursor = state.cursor
        start_time = state.start_time
        # Overlap guard: single-flight per account. If another cycle holds the
        # lock, skip rather than race (both the manual endpoint and the poll loop
        # funnel through here, so this covers every trigger).
        if not await self.state_repo.try_acquire_lock(
            db, subdomain, stale_after_seconds=SYNC_LOCK_STALE_SECONDS
        ):
            logger.info("Zendesk sync skipped: another cycle already holds the lock.")
            return SyncResult(
                skipped=True,
                skipped_reason="another sync cycle is already in progress",
                cursor=initial_cursor,
            )
        result = SyncResult(cursor=initial_cursor)
        owns_client = client is None
        if owns_client:
            client = httpx.AsyncClient(timeout=60)

        base = self._provider_obj().base_url
        page_limit = max_pages if max_pages is not None else settings.ZENDESK_MAX_PAGES_PER_CYCLE
        page_size = per_page if per_page is not None else settings.ZENDESK_SYNC_PER_PAGE
        cursor = initial_cursor
        try:
            pages_done = 0
            while pages_done < page_limit:
                params: dict = {
                    "include": "users",
                    "per_page": page_size,
                }
                if cursor:
                    params["cursor"] = cursor
                else:
                    params["start_time"] = start_time or 1

                data = await self._get(client, base + INCREMENTAL_PATH, params, sleep)
                users_map = _index_users(data.get("users", []))
                tickets = data.get("tickets", [])
                result.pages += 1
                pages_done += 1

                for ticket in tickets:
                    result.tickets_seen += 1
                    try:
                        await self._process_ticket(
                            db, client, ticket, users_map, result, sleep,
                            allowed_statuses,
                        )
                    except Exception as exc:  # noqa: BLE001 - one ticket must not halt the batch
                        result.failed += 1
                        result.errors.append(f"ticket {ticket.get('id')}: {exc}")
                        logger.exception(
                            "Zendesk ticket %s failed; skipping.", ticket.get("id")
                        )

                cursor = data.get("after_cursor") or cursor
                # Checkpoint the cursor after every page so a restart resumes.
                await self.state_repo.update_state(
                    db, state, cursor=cursor, set_cursor=True
                )
                result.cursor = cursor

                if data.get("end_of_stream"):
                    break
                await sleep(PAGE_SLEEP_SECONDS)

            await self.state_repo.update_state(
                db,
                state,
                last_synced_at=datetime.now(timezone.utc),
                last_error=None,
                set_last_error=True,
                add_seen=result.tickets_seen,
            )
        except Exception as exc:
            await self.state_repo.update_state(
                db, state, last_error=str(exc), set_last_error=True
            )
            raise
        finally:
            if owns_client:
                await client.aclose()
            # Always release the single-flight lock, even on failure, so a crash
            # doesn't strand it (the staleness takeover is only the backstop).
            await self.state_repo.release_lock(db, subdomain)

        return result


async def run_sync_cycle(
    db: AsyncSession,
    *,
    client: httpx.AsyncClient | None = None,
    max_pages: int | None = None,
    per_page: int | None = None,
    statuses: list[str] | None = None,
) -> SyncResult:
    """Run one Zendesk poll cycle. The SHARED entry point.

    Both the manual ``POST /api/v1/zendesk/sync`` endpoint and the background
    polling loop call this — so a future webhook trigger is just one more caller,
    not a redesign. ``max_pages``/``per_page`` allow a caller (e.g. a controlled
    HTTP test run) to bound the cycle; ``statuses`` optionally overrides the
    ZENDESK_SYNC_STATUSES allow-list for that call only. All default to the
    configured values, so the poll loop (which passes none of them) is unaffected.
    """
    return await ZendeskIngestAdapter().sync(
        db, client=client, max_pages=max_pages, per_page=per_page, statuses=statuses
    )


async def zendesk_poll_loop(
    stop_event: asyncio.Event,
    *,
    interval: int | None = None,
    session_factory=None,
    sleep=asyncio.sleep,
) -> None:
    """Background loop: run a sync cycle every ``interval`` seconds until stopped.

    Each cycle gets its own DB session (not request-scoped). A cycle failure is
    logged and the loop continues. The wait between cycles is interruptible so
    shutdown is prompt.
    """
    interval = interval if interval is not None else settings.ZENDESK_POLL_INTERVAL_SECONDS
    factory = session_factory or async_session_factory
    logger.info("Zendesk poll loop started (interval=%ss).", interval)
    while not stop_event.is_set():
        try:
            async with factory() as db:
                res = await run_sync_cycle(db)
            logger.info("Zendesk poll cycle: %s", res.model_dump())
        except Exception:  # noqa: BLE001 - a cycle failure must not kill the loop
            logger.exception("Zendesk poll cycle failed.")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    logger.info("Zendesk poll loop stopped.")
