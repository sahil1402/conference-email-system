"""Email API (v1) — ingest, queue, detail, and chair actions.

Thin HTTP layer over the pipeline and repositories. Follows the app's existing
router pattern: a module-level ``router = APIRouter(...)`` mounted by main.py,
and the ``get_db`` dependency from ``app.db.database`` for the async session.
No SQLAlchemy is touched directly here — all persistence goes through the
repositories, all processing through EmailPipeline.
"""

import asyncio
import html as _html
import json
import logging
import re

import bleach
from datetime import datetime, time, timezone
from typing import Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.events import get_event_broker
from app.core.send_gate import authorize_send
from app.core.tracing import read_traces
from app.db.database import async_session_factory, get_db
from app.integrations.zendesk.adapter import ZendeskIngestAdapter
from app.integrations.zendesk.sender import (
    ZendeskSender,
    ZendeskSendError,
)
from app.models.enums import EmailSource, EmailStatus
from app.pipeline.active_learning import build_flag_events
from app.db.models import AuditLog, Email
from app.pipeline.drafter import find_placeholders
from app.pipeline.orchestrator import EmailPipeline, resolve_lineage_roots
from app.pipeline.rl_router import get_rl_router
from app.repositories.audit_repository import AuditRepository
from app.repositories.chair_repository import ChairRepository
from app.repositories.email_repository import EmailRepository
from app.repositories.policy_repository import PolicyRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/emails", tags=["emails"])

email_repo = EmailRepository()
audit_repo = AuditRepository()
chair_repo = ChairRepository()
policy_repo = PolicyRepository()
# Module-level so tests can monkeypatch the transport without real HTTP.
zendesk_sender = ZendeskSender()
# Same — the post-send reconcile (single-ticket re-sync) is best-effort and
# stubbed in tests so no real HTTP happens.
zendesk_adapter = ZendeskIngestAdapter()


class SendRequest(BaseModel):
    """Options for releasing a draft (Zendesk write-back)."""

    # Default is the safe internal note. A public reply also requires
    # ALLOW_AUTO_SEND=True (enforced in the endpoint), per ZENDESK_API.md §4.
    public: bool = Field(
        default=False,
        description="True = public reply to the requester (needs ALLOW_AUTO_SEND); "
        "False = internal note (default, safe).",
    )
    sent_by: str = Field(default="chair", description="Actor recorded in the audit log.")
    # Reserved (not yet consumed): the Zendesk status to set on send. Mirrors the
    # frontend ApproveRequest.target_status naming/values (types/index.ts). Inert
    # in this piece — /send still hardcodes status behavior until a later piece.
    target_status: Literal["open", "pending", "solved"] | None = Field(
        default=None,
        description="Zendesk ticket status to set on send (reserved; currently unused).",
    )


class RedraftRequest(BaseModel):
    """Options for a manual re-draft (all optional — an absent body is a plain retry)."""

    forced_policy_key: str | None = Field(
        default=None,
        description="Policy key the chair wants grounded in the new draft, in "
        "ADDITION to normal retrieval (a guaranteed extra slot, never evicted by "
        "re-ranking). Must be an ACTIVE policy; an unknown, retired, or superseded "
        "key is logged and ignored rather than failing the re-draft.",
    )
    excluded_policy_ids: list[str] | None = Field(
        default=None,
        max_length=10,
        description="Policy ids the chair removed from this draft's grounding. "
        "Applied as a filter to the RANKED chunks before the forced-policy slot "
        "is appended, so the drafter simply never sees them — there is no prompt "
        "representation of an exclusion. Capped at 10: the ranked set is bounded "
        "by MAX_RETRIEVED_CHUNKS, so a longer list can only be malformed input.",
    )


class SetStatusRequest(BaseModel):
    """Options for setting a ticket's Zendesk status WITHOUT sending a reply."""

    status: Literal["new", "open", "pending", "solved"] = Field(
        default="solved",
        description="The Zendesk status to set (no reply is sent). "
        "Only 'solved' has a keyboard shortcut; 'new'/'open'/'pending' are "
        "button-only.",
    )
    set_by: str = Field(
        default="chair", description="Actor recorded in the audit log."
    )


def _text_to_html(text: str) -> str:
    """Render a plain-text draft as minimal safe HTML (preferred body per §4).

    Escapes the text, then maps blank lines to paragraph breaks and single
    newlines to ``<br>`` so the reply keeps its shape in Agent Workspace.
    """
    escaped = _html.escape(text or "").strip()
    if not escaped:
        return "<p></p>"
    paragraphs = [p.replace("\n", "<br>") for p in escaped.split("\n\n")]
    return "".join(f"<p>{p}</p>" for p in paragraphs)


# Allowlist for sanitizing Zendesk-authored comment HTML before it reaches the
# chair's browser. Comment bodies are requester/agent-authored → an XSS surface,
# so only formatting tags survive; bleach strips scripts, <style>, inline event
# handlers, style attributes, and any tag/attr/protocol not listed here.
_HTML_ALLOWED_TAGS = [
    "p", "br", "div", "span", "a", "ul", "ol", "li", "b", "strong", "i", "em",
    "u", "s", "blockquote", "pre", "code", "h1", "h2", "h3", "h4", "h5", "h6",
    "hr", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "img",
]
_HTML_ALLOWED_ATTRS = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan"],
}
# http/https/mailto/tel/cid are allowed anywhere; ``data:`` is permitted ONLY on
# <img src> and ONLY for safe raster types (see ``_allow_attribute``) — this lets
# inline screenshots embedded as base64 render while blocking data:svg (which can
# carry script) and data: on links.
_HTML_ALLOWED_PROTOCOLS = ["http", "https", "mailto", "tel", "cid", "data"]
_IMG_DATA_URI_RE = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,", re.I)
# Drop <script>/<style> blocks WITH their contents first: bleach removes the
# tags but would otherwise leave their inner JS/CSS as visible literal text.
_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)\b[^>]*>.*?</\1>")


def _allow_attribute(tag: str, name: str, value: str) -> bool:
    """Attribute allowlist for bleach, with a guarded exception for data: URIs.

    Enforces :data:`_HTML_ALLOWED_ATTRS`, and additionally permits a ``data:``
    URI only on ``<img src>`` and only for safe raster image types — never on
    links or any other attribute (blocks data:text/html, data:image/svg+xml).
    """
    if name not in _HTML_ALLOWED_ATTRS.get(tag, ()):  # noqa: SIM118 - dict.get
        return False
    v = (value or "").strip()
    if v[:5].lower() == "data:":
        return tag == "img" and name == "src" and bool(_IMG_DATA_URI_RE.match(v))
    return True


def _sanitize_html(raw: str | None) -> str | None:
    """Sanitize Zendesk comment HTML for safe in-browser rendering.

    Returns cleaned HTML (formatting allowlist only), or ``None`` when there is
    no HTML so the caller can fall back to the plain-text body.
    """
    if not raw:
        return None
    stripped = _SCRIPT_STYLE_RE.sub("", raw)
    return bleach.clean(
        stripped,
        tags=_HTML_ALLOWED_TAGS,
        attributes=_allow_attribute,
        protocols=_HTML_ALLOWED_PROTOCOLS,
        strip=True,
    )


def _iso_z(dt) -> str | None:
    """Format an aware datetime as a Zendesk ISO-8601 ``...Z`` stamp, or None."""
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_rl_feedback(email: Email, lane: str | None, outcome: str) -> None:
    """Feed a chair decision to the RL bandit. Never raises.

    The bandit learns from real approve/reroute signals; a failure here must
    not break the chair's action, so everything is best-effort.
    """
    try:
        intent = (email.classification or {}).get("intent")
        if intent and lane:
            get_rl_router().record_feedback(intent=intent, action=lane, outcome=outcome)
    except Exception:  # noqa: BLE001 - feedback is best-effort
        logger.warning("RL feedback recording failed (%s).", outcome, exc_info=True)


async def _record_flag_events(
    db: AsyncSession,
    email_id: str,
    actor: str,
    classification,
    *,
    was_edited: bool = False,
    original_text: str = "",
    edited_text: str = "",
) -> None:
    """Write active-learning candidate flags to the audit log (best-effort).

    Each fired signal becomes its own audit entry with a distinct action type
    (flagged_low_confidence / flagged_meaningful_edit) so the two stay separate.
    Flags candidates for future human labeling only — no retraining is triggered.
    """
    try:
        events = build_flag_events(
            classification,
            was_edited=was_edited,
            original_text=original_text,
            edited_text=edited_text,
        )
        for action, details in events:
            await audit_repo.log_action(db, email_id, action, actor, details)
    except Exception:  # noqa: BLE001 - flagging must never break the chair action
        logger.warning("Active-learning flagging failed.", exc_info=True)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class IngestEmailRequest(BaseModel):
    """Inbound email payload. ``from``/``to`` are reserved-ish words, so they
    arrive under aliases and bind to safe attribute names."""

    model_config = ConfigDict(populate_by_name=True)

    from_email: str = Field(alias="from")
    to_email: str = Field(alias="to")
    subject: str
    body: str
    timestamp: str = ""


class ApproveRequest(BaseModel):
    approved_by: str
    final_text: str | None = None


class RerouteRequest(BaseModel):
    rerouted_by: str
    reason: str
    new_lane: str


class ReassignChairRequest(BaseModel):
    reassigned_by: str
    new_chair_id: int
    reason: str = ""


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------
def _forced_policy_applied(email: Email) -> bool | None:
    """Did the chair's forced policy make it into the current draft's grounding?

    DERIVED, never stored as its own column: the answer is exactly
    ``forced_policy_key in retrieved_ids``, both of which already live in
    ``retrieval_context``. Keeping it computed means it can never drift out of
    sync with the grounding set it describes (same principle as hydrating
    ``retrieved_chunks`` on read rather than duplicating chunk text).

    ``None``  — no forced policy was requested for this draft (a plain redraft,
                or any draft generated before manual invoke existed). This is the
                unchanged default, so old rows are unaffected.
    ``True``  — requested and present in the grounding set.
    ``False`` — requested but skipped: unknown key, not active, or the lookup
                failed. The chair asked for something they did not get, which is
                the case the UI needs to surface.
    """
    ctx = email.retrieval_context or {}
    key = ctx.get("forced_policy_key")
    if not key:
        return None
    return key in (ctx.get("retrieved_ids") or [])


def _email_to_dict(email: Email) -> dict:
    """Serialize an Email ORM row (including its JSON pipeline columns)."""
    return {
        "id": email.id,
        "sender": email.sender,
        "sender_name": email.sender_name,
        "subject": email.subject,
        "body": email.body,
        "status": email.status,
        "received_at": email.received_at.isoformat() if email.received_at else None,
        "assigned_chair_id": email.assigned_chair_id,
        "source": email.source,
        "zendesk_ticket_id": email.zendesk_ticket_id,
        # Deep link to the ticket in the Zendesk agent UI. Built from the
        # existing ZENDESK_SUBDOMAIN config so the frontend needs no Zendesk env
        # var. Null unless this row has a ticket id AND a subdomain is configured.
        "zendesk_ticket_url": (
            f"https://{settings.ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{email.zendesk_ticket_id}"
            if email.zendesk_ticket_id is not None and settings.ZENDESK_SUBDOMAIN
            else None
        ),
        "zendesk_status": email.zendesk_status,
        "classification": email.classification,
        "routing": email.routing,
        "draft": email.draft,
        "redrafting": bool(email.redrafting),
        "retrieval_context": email.retrieval_context,
        # Derived from retrieval_context (no column, no duplicated state). Sits
        # beside `redrafting` on purpose: the frontend already refetches the email
        # when that flag clears, so the manual-invoke outcome arrives on the very
        # same read — no extra endpoint and no separate poll.
        "forced_policy_applied": _forced_policy_applied(email),
        "created_at": email.created_at.isoformat() if email.created_at else None,
        "updated_at": email.updated_at.isoformat() if email.updated_at else None,
    }


async def _email_detail_dict(db: AsyncSession, email: Email) -> dict:
    """``_email_to_dict`` plus the hydrated grounding set (detail views only).

    ``retrieved_chunks`` is NOT a stored column. The pipeline persists only
    ``retrieval_context.retrieved_ids`` (rank-ordered), so the full chunks are
    resolved on READ by joining those ids against ``policy_documents``. That
    keeps a single source of truth for policy text — an edited policy's chunk
    body is never a stale copy frozen into the email row.

    Distinct from ``draft.citations`` (what the model CLAIMS it cited, often
    empty). This is what retrieval actually grounded the draft on.

    Deliberately NOT folded into ``_email_to_dict``: that serializer also runs
    per-row over the queue page, where a lookup would be an N+1 across up to
    ``limit`` emails. Hydration is one extra query, on single-email reads only.
    """
    data = _email_to_dict(email)
    data["retrieved_chunks"] = await _hydrate_retrieved_chunks(db, email)
    return data


async def _hydrate_retrieved_chunks(db: AsyncSession, email: Email) -> list[dict] | None:
    """Resolve ``retrieval_context.retrieved_ids`` into full chunk dicts.

    Returns None when the email has no retrieval context (never processed, or a
    legacy row back-filled NULL by migration ``c1d2e3f4a5b6``) — the absence of
    a grounding set is different from an empty one, and the UI already treats
    null as "nothing to show". Rank order follows ``retrieved_ids``.

    ``score`` is intentionally OMITTED: the pipeline never persisted per-chunk
    scores, so there is no honest value to serve for existing rows. Rank is
    conveyed by list position instead of a fabricated number.

    Best-effort — a lookup failure logs and yields None rather than 500-ing an
    email the chair is trying to read.
    """
    ctx = email.retrieval_context
    if not ctx:
        return None
    ids = [pid for pid in (ctx.get("retrieved_ids") or []) if pid]
    if not ids:
        return []

    try:
        rows = await policy_repo.get_by_keys(db, ids)
    except Exception:  # noqa: BLE001 - never break a detail read over grounding
        logger.exception("Failed to hydrate retrieved chunks for email %s", email.id)
        return None

    # Preserve retrieval rank; skip ids with no row (hard-deleted out of band —
    # retire/edit are soft, so this is not reachable through the KB API today).
    return [
        {
            "policy_id": row.policy_key,
            "title": row.title or "",
            "content": row.content or "",
            "category": row.category or "",
        }
        for pid in ids
        if (row := rows.get(pid)) is not None
    ]


def _audit_to_dict(entry: AuditLog) -> dict:
    return {
        "id": entry.id,
        "email_id": str(entry.email_id),
        "action": entry.action,
        "actor": entry.actor,
        "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
        "metadata": entry.extra_metadata,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/ingest")
async def ingest_email(
    payload: IngestEmailRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Run an inbound email through the full pipeline and persist it."""
    email_data = {
        "from": payload.from_email,
        "to": payload.to_email,
        "subject": payload.subject,
        "body": payload.body,
        "timestamp": payload.timestamp,
    }
    pipeline = EmailPipeline()
    try:
        result = await pipeline.process_email(email_data, db)
    except Exception as exc:  # noqa: BLE001 - surface pipeline failure as 500
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pipeline processing failed: {exc}",
        ) from exc
    return result.model_dump()


# --- received_at range parsing -------------------------------------------
# Contract for the date-range filter, single-sourced here because BOTH /queue
# and /queue/facets must interpret the values identically or the counts beside
# the list would describe a different window than the list itself.
#
# A bare date is DELIBERATELY accepted (chairs pick days, not instants) and is
# expanded to cover the whole day: ``received_after`` -> 00:00:00.000000,
# ``received_before`` -> 23:59:59.999999. Requiring full timestamps was the
# alternative and was rejected — it pushes the same expansion onto every caller,
# and a client that forgets it silently loses almost a full day of tickets
# against the repository's INCLUSIVE ``<=``, with no error to notice.
#
# A value carrying an explicit time is used verbatim (never re-expanded), so a
# caller who wants a precise instant still gets one.
_RECEIVED_PARAM_CONTRACT = (
    "Accepts a bare date (YYYY-MM-DD) or a full ISO-8601 timestamp. A bare date "
    "covers the WHOLE day in local-to-UTC terms: `received_after` starts at "
    "00:00:00 and `received_before` ends at 23:59:59.999999. A timestamp is used "
    "exactly as given. Naive values (no offset) are read as UTC. Both bounds are "
    "inclusive."
)


def _parse_received_bound(
    raw: str | None, *, param: str, end_of_day: bool
) -> datetime | None:
    """Parse a ``received_after`` / ``received_before`` query value.

    Returns ``None`` for an absent/blank value (filter not applied). Raises 422
    for anything unparseable, rather than degrading to "no filter" — a chair who
    mistypes a date must see an error, not a silently unfiltered queue.

    Date-only input is widened to the correct end of the day (see the contract
    above). Naive input is stamped UTC, matching the convention the ingest path
    already uses (``orchestrator._parse_received_at``), so a value means the same
    instant no matter which entry point produced it.
    """
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            # Literal 422, not a starlette constant: HTTP_422_UNPROCESSABLE_ENTITY
            # is deprecated on current starlette (warns on every call) while its
            # replacement HTTP_422_UNPROCESSABLE_CONTENT does not exist on older
            # versions that `fastapi>=0.111` still permits. The integer is correct
            # on both.
            status_code=422,
            detail=(
                f"Invalid {param}: {raw!r}. Expected YYYY-MM-DD or an ISO-8601 "
                "timestamp such as 2026-01-20T14:30:00Z."
            ),
        ) from None
    # "date only" is decided on the RAW TEXT, not on the parsed value: a caller
    # who explicitly asked for 2026-01-20T00:00:00 means midnight and must not
    # have it silently widened to the end of the day.
    is_date_only = "T" not in text and " " not in text
    if is_date_only and end_of_day:
        parsed = datetime.combine(parsed.date(), time.max, tzinfo=parsed.tzinfo)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _received_range(
    received_after: str | None, received_before: str | None
) -> tuple[datetime | None, datetime | None]:
    """Parse both bounds and reject an inverted range with a 422.

    Without this check an inverted range is indistinguishable from a genuinely
    empty window: the repository returns 0 rows either way, so a chair who typed
    the dates backwards would conclude there are no tickets rather than that the
    request was wrong.
    """
    after = _parse_received_bound(
        received_after, param="received_after", end_of_day=False
    )
    before = _parse_received_bound(
        received_before, param="received_before", end_of_day=True
    )
    if after is not None and before is not None and after > before:
        raise HTTPException(
            status_code=422,  # see _parse_received_bound on the literal
            detail=(
                f"received_after ({after.isoformat()}) must be on or before "
                f"received_before ({before.isoformat()})."
            ),
        )
    return after, before


@router.get("/queue")
async def get_queue(
    lane: str | None = None,
    chair_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    unassigned: bool = False,
    source: str | None = None,
    zendesk_status: str | None = None,
    received_after: str | None = Query(None, description=_RECEIVED_PARAM_CONTRACT),
    received_before: str | None = Query(None, description=_RECEIVED_PARAM_CONTRACT),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the email queue, filtered server-side by any combination of
    lane / chair / unassigned / status / source / zendesk_status / search /
    received_at range.

    ``total`` is the count for the SAME filter set (not the whole table), so a
    scoped caller gets an accurate total independent of ``limit``/``offset`` and
    the returned rows are the full server-side slice — callers never filter or
    count a truncated page client-side.

    ``limit`` is bounded 1..200 and ``offset`` >= 0 (FastAPI returns 422 for
    out-of-range values), so no caller can request an unbounded page or a
    negative offset.

    ``received_after`` / ``received_before`` bound ``received_at`` inclusively
    (see :data:`_RECEIVED_PARAM_CONTRACT`); an inverted range is a 422, not an
    empty page. The RESOLVED datetimes are echoed in ``page_info``, so a client
    can see exactly which window was applied after any end-of-day expansion.
    """
    after, before = _received_range(received_after, received_before)
    kwargs = dict(
        lane=lane,
        chair_id=chair_id,
        status=status,
        search=search,
        unassigned=unassigned,
        source=source,
        zendesk_status=zendesk_status,
        received_after=after,
        received_before=before,
    )
    emails = await email_repo.get_email_queue(db, limit=limit, offset=offset, **kwargs)
    total = await email_repo.count_email_queue(db, **kwargs)
    return {
        "emails": [_email_to_dict(e) for e in emails],
        "total": total,
        "page_info": {"limit": limit, "offset": offset, **kwargs},
    }


@router.get("/queue/facets")
async def get_queue_facets(
    lane: str | None = None,
    chair_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    unassigned: bool = False,
    received_after: str | None = Query(None, description=_RECEIVED_PARAM_CONTRACT),
    received_before: str | None = Query(None, description=_RECEIVED_PARAM_CONTRACT),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return grouped facet counts for the queue's status bar + source toggle.

    A dedicated server-side aggregate (see EmailRepository.count_queue_facets),
    NOT a tally over a capped queue page — that page-derived pattern drops
    out-of-window rows (Phase 6C bug class). The context filters
    (lane / chair / unassigned / status / search / received_at range) are honored
    so the facets compose with the queue's other filters; the facet dimensions
    themselves (source, zendesk_status) are intentionally not applied so the bar
    always shows every status and the toggle always sees every source.

    The date range is parsed by the SAME helper as ``/queue``
    (:func:`_received_range`), so the counts always describe the identical
    window as the list they sit beside — including the end-of-day expansion.

    Response shape::

        {
          "by_zendesk_status": {"new": 3, "open": 2, "solved": 1},
          "by_source": {"zendesk": 6, "toy_dataset": 47},
          "sources": ["toy_dataset", "zendesk"]
        }
    """
    after, before = _received_range(received_after, received_before)
    return await email_repo.count_queue_facets(
        db,
        lane=lane,
        chair_id=chair_id,
        status=status,
        search=search,
        unassigned=unassigned,
        received_after=after,
        received_before=before,
    )


# Seconds between SSE heartbeat comments when no events are flowing — keeps the
# connection (and any intermediary proxies) from idling out, and lets the client
# notice a dropped connection promptly.
_SSE_HEARTBEAT_SECONDS = 15.0


@router.get("/stream")
async def stream_emails(request: Request) -> StreamingResponse:
    """Server-Sent Events stream of email lifecycle changes.

    Emits one ``data:`` event per audit-logged state change (created,
    classified/routed, drafted, approved, rerouted) so the review queue can
    update live instead of waiting for its 15s poll. A heartbeat comment is sent
    when idle. One-directional and in-process — no WebSocket, no broker.
    """
    broker = get_event_broker()
    queue = broker.add_subscriber()

    async def event_generator():
        # Opening comment so the client's onopen fires immediately.
        yield ": connected\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=_SSE_HEARTBEAT_SECONDS
                    )
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # No events for a while — send a heartbeat comment.
                    yield ": ping\n\n"
        finally:
            broker.remove_subscriber(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering (e.g. nginx)
        },
    )


@router.get("/by-ticket/{ticket_id}")
async def get_email_by_ticket(
    ticket_id: int, db: AsyncSession = Depends(get_db)
) -> dict:
    """Return one email (by its Zendesk ticket id) with its full audit trail.

    A two-segment static path, so it MUST be declared before the ``/{email_id}``
    catch-all below — otherwise ``by-ticket`` is captured as an ``email_id`` and
    this route never matches. ``ticket_id`` is typed ``int``, so a non-numeric
    value is rejected by FastAPI with a 422 before this handler runs. The
    response shape mirrors ``GET /emails/{email_id}`` exactly (same helpers).

    404s when no email maps to ``ticket_id`` — including a row whose
    ``zendesk_ticket_id`` is NULL (non-Zendesk), which the repository query never
    matches.
    """
    email = await email_repo.get_email_by_zendesk_ticket_id(db, ticket_id)
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No email found for ticket id {ticket_id}",
        )
    trail = await audit_repo.get_audit_trail(db, str(email.id))
    return {
        "email": await _email_detail_dict(db, email),
        "audit_trail": [_audit_to_dict(a) for a in trail],
    }


@router.get("/{email_id}")
async def get_email(
    email_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    """Return one email together with its full audit trail."""
    email = await email_repo.get_email_by_id(db, email_id)
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email {email_id} not found",
        )
    trail = await audit_repo.get_audit_trail(db, email_id)
    return {
        "email": await _email_detail_dict(db, email),
        "audit_trail": [_audit_to_dict(a) for a in trail],
    }


@router.get("/{email_id}/thread")
async def get_email_thread(
    email_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    """Return one ticket's full multi-turn conversation, oldest-first.

    Includes internal notes (``public`` False) so the review UI can show them
    distinctly. Non-Zendesk emails simply have no thread rows → ``[]``. 404s if
    the email itself is unknown.
    """
    email = await email_repo.get_email_by_id(db, email_id)
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email {email_id} not found",
        )
    messages = await email_repo.get_thread_messages(db, email_id)
    # The requester is identified by author_id == ticket requester_id — NOT by
    # Zendesk's ``role``, since chairs/agents often have role "end-user" in this
    # account (so role alone mislabels their replies as the requester). None when
    # there is no Zendesk requester (non-Zendesk email) → UI falls back to role.
    requester_id = email.zendesk_requester_id
    return {
        "messages": [
            {
                **m,
                "created_at": m["created_at"].isoformat() if m["created_at"] else None,
                # Server-sanitized HTML for rich rendering; None → UI falls back
                # to plain_body. Requester-authored, so sanitize before exposing.
                "html_body": _sanitize_html(m.get("html_body")),
                "is_requester": (
                    (m.get("author_id") == requester_id)
                    if requester_id is not None else None
                ),
            }
            for m in messages
        ]
    }


@router.get("/{email_id}/trace")
async def get_email_trace(
    email_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    """Return the ordered per-stage pipeline trace for one email.

    The trace records (classify → retrieve → route → draft) are read from the
    structured trace log, oldest first. 404s if the email itself is unknown.
    """
    email = await email_repo.get_email_by_id(db, email_id)
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email {email_id} not found",
        )
    entries = read_traces(str(email.id))
    return {
        "email_id": str(email.id),
        "count": len(entries),
        "trace": entries,
    }


async def _learn_from_edit_bg(email_id: str) -> None:
    """Background CEL entry point: learn a candidate policy from a chair's
    [CHAIR:]-gap-filling edit.

    ``learn_from_edit`` opens its own session and never raises (best-effort),
    so this wrapper exists only to give ``background_tasks.add_task`` a plain
    ``(email_id)`` callable — no session/error handling needed here.
    """
    from app.pipeline.experience_learning import learn_from_edit

    await learn_from_edit(email_id)


@router.patch("/{email_id}/approve")
async def approve_email(
    email_id: str,
    payload: ApproveRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Approve an email's draft, preserving the diff when the chair edited it.

    When ``final_text`` differs from the current draft, the original AI/template
    draft is preserved (``draft.original_draft_text``), the edited text becomes
    the new ``draft.draft_text``, and the audit entry captures BOTH full texts so
    the diff can be reconstructed later (Phase 5G active-learning signal).
    Approving unchanged text is NOT recorded as an edit (identical ≠ an edit).
    """
    existing = await email_repo.get_email_by_id(db, email_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email {email_id} not found",
        )

    draft = dict(existing.draft or {})
    current_text = draft.get("draft_text", "") or ""
    # The true original is the first AI/template draft — preserved across edits.
    original_text = draft.get("original_draft_text") or current_text
    final_text = payload.final_text
    edited = final_text is not None and final_text.strip() != current_text.strip()

    # Send-gate: a reply may not go out while [CHAIR: ...] placeholders remain
    # — the chair must replace each one with real content (or delete it) first.
    unresolved = find_placeholders(final_text if final_text is not None else current_text)
    if unresolved:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Draft still contains unresolved [CHAIR: ...] "
                "placeholders; fill them in before approving.",
                "placeholders": unresolved,
            },
        )

    updates: dict = {}
    details: dict = {"edited": edited}
    if edited:
        draft["original_draft_text"] = original_text
        draft["draft_text"] = final_text
        draft["is_edited"] = True
        draft["edited_by"] = payload.approved_by
        updates["draft"] = draft
        # Keep BOTH full texts so the diff is reconstructable (never lose either).
        details["original_draft"] = original_text
        details["edited_draft"] = final_text
    elif final_text is not None:
        details["final_text"] = final_text

    updated = await email_repo.update_email_status(
        db, email_id, "approved", updates
    )
    await audit_repo.log_action(
        db, email_id, "approved", payload.approved_by, details
    )
    # The approved lane was the right call → reward that (intent, lane) arm.
    _record_rl_feedback(updated, (updated.routing or {}).get("lane"), "approved")
    # Flag active-learning candidates (near-miss confidence and/or a meaningful edit).
    await _record_flag_events(
        db,
        email_id,
        payload.approved_by,
        existing.classification,
        was_edited=edited,
        original_text=original_text,
        edited_text=final_text or "",
    )
    # CEL: an edit that resolved a [CHAIR: ...] gap is exactly the signal the
    # experience-learning stage looks for (a chair supplying knowledge the AI
    # didn't have) — schedule the best-effort background learner. A plain
    # unchanged approve, or an edit with no such gap, schedules nothing.
    if edited and find_placeholders(original_text):
        background_tasks.add_task(_learn_from_edit_bg, str(updated.id))
    return _email_to_dict(updated)


@router.post("/{email_id}/send")
async def send_email_reply(
    email_id: str,
    payload: SendRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Release an email's draft to the outbound transport — gate enforced.

    The send gate (app/core/send_gate.py) always decides first, and both
    outcomes are audited. For a Zendesk-sourced email the authorized draft is
    then written back to the ticket (internal note by default; public reply only
    when ALLOW_AUTO_SEND is on AND explicitly requested — §4). Non-Zendesk emails
    have no transport yet and still answer 501. A Zendesk write failure marks the
    email ``send_failed`` (re-triable) rather than falsely showing it as sent.
    """
    payload = payload or SendRequest()
    email = await email_repo.get_email_by_id(db, email_id)
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email {email_id} not found",
        )

    decision = authorize_send(email)
    await audit_repo.log_action(
        db, email_id,
        "send_authorized" if decision.authorized else "send_blocked",
        "send_gate",
        {"mode": decision.mode, "reason": decision.reason},
    )
    if not decision.authorized:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Send refused by the send gate.",
                    "reason": decision.reason},
        )

    # Non-Zendesk emails: no outbound transport exists yet — behavior unchanged.
    if (email.source or "") != EmailSource.ZENDESK.value or not email.zendesk_ticket_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"message": "Send authorized, but no outbound transport is "
                    "configured for this source; the draft remains queued.",
                    "mode": decision.mode},
        )

    # Closed tickets are immutable (§2) — never attempt a write; report clearly.
    if (email.zendesk_status or "").lower() == "closed":
        await audit_repo.log_action(
            db, email_id, "send_blocked_closed", payload.sent_by,
            {"zendesk_ticket_id": email.zendesk_ticket_id},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Zendesk ticket is closed and immutable; cannot "
                    "write. Annotate via a follow-up ticket instead.",
                    "zendesk_status": email.zendesk_status},
        )

    # Public reply vs internal note = whether the requester is notified. WHO may
    # send has already been enforced by the send gate above (authorize_send): an
    # approved draft may go public regardless of ALLOW_AUTO_SEND, while an
    # unreviewed draft_generated draft is refused there (409) unless the FAQ-lane
    # auto path is unlocked. So no additional visibility gate is needed here.
    want_public = bool(payload.public)

    draft_text = (email.draft or {}).get("draft_text", "") or ""
    html_body = _text_to_html(draft_text)
    # Status: an explicit chair-chosen target_status wins (independent of the
    # public/internal decision — e.g. "pending" on an internal note). When none
    # is supplied, fall back to the §4 default: public reply → "solved"; internal
    # note → leave status unchanged. Tags track state via the dedicated tag endpoint.
    set_status = payload.target_status if payload.target_status is not None else (
        "solved" if want_public else None
    )
    # Tag by the AI draft's fate, not merely by visibility: ``ai_auto_replied``
    # ONLY when the AI's words reached the requester UNTOUCHED — a public reply
    # the chair did not edit. A human-edited reply, or ANY internal note (never
    # sent to the requester), is ``ai_drafted``. ``is_edited`` is set by the
    # approve endpoint when the chair's final text differs from the AI draft;
    # automatic sanitization (e.g. the [Sender name] fill) is part of the AI
    # draft and so is not counted as an edit.
    is_edited = bool((email.draft or {}).get("is_edited"))
    tags = ["ai_auto_replied"] if (want_public and not is_edited) else ["ai_drafted"]
    updated_stamp = _iso_z(email.zendesk_updated_at)

    try:
        outcome = await zendesk_sender.send_reply(
            ticket_id=int(email.zendesk_ticket_id),
            html_body=html_body,
            public=want_public,
            set_status=set_status,
            tags=tags,
            updated_stamp=updated_stamp,
        )
    except ZendeskSendError as exc:
        # Transport failed — record the failure locally so it never reads as
        # "sent", and keep the draft intact so the chair can retry.
        send_meta = {
            "state": "failed",
            "public": want_public,
            "error": str(exc),
            "status_code": exc.status_code,
        }
        failed_draft = {**(email.draft or {}), "send": send_meta}
        await email_repo.update_email_status(
            db, email_id, EmailStatus.SEND_FAILED.value, {"draft": failed_draft}
        )
        await audit_repo.log_action(
            db, email_id, "send_failed", payload.sent_by, send_meta
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": "Zendesk write failed; email marked send_failed "
                    "and left re-triable.", "error": str(exc)},
        ) from exc

    # Success — record what was sent and flip status to sent.
    send_meta = {
        "state": "sent",
        "mode": outcome.mode,
        "public": outcome.public,
        "status_set": outcome.status_set,
        "tags_added": outcome.tags_added,
        "tag_conflict": outcome.tag_conflict,
    }
    sent_draft = {**(email.draft or {}), "send": send_meta}
    await email_repo.update_email_status(
        db, email_id, EmailStatus.SENT.value, {"draft": sent_draft}
    )
    await audit_repo.log_action(
        db, email_id, "zendesk_sent", payload.sent_by, send_meta
    )

    # Reconcile the local Zendesk state so the ticket moves buckets immediately
    # and its thread shows the reply we just posted, without waiting for the next
    # background poll. First optimistically mirror the status we set (this alone
    # guarantees the bucket move even if the re-sync below can't reach Zendesk),
    # then best-effort re-sync THIS ticket for the authoritative status + to append
    # the outbound comment to the thread. A re-sync failure must NEVER turn a
    # successful send into an error — the reply is already posted to Zendesk.
    try:
        if set_status is not None:
            await email_repo.apply_zendesk_fields(
                db, email_id, {"zendesk_status": set_status}
            )
        await zendesk_adapter.refresh_ticket(db, int(email.zendesk_ticket_id))
    except Exception as exc:  # noqa: BLE001 - reconcile is best-effort
        logger.warning(
            "post-send reconcile of ticket %s failed (reply was sent): %s",
            email.zendesk_ticket_id, exc,
        )
        # A failed DB write inside the reconcile leaves the session in a
        # pending-rollback state; clear it so the read below (and the rest of
        # this request) can't raise PendingRollbackError. Any already-committed
        # optimistic status write stands, so the bucket move persists.
        await db.rollback()

    updated = await email_repo.get_email_by_id(db, email_id)
    result = _email_to_dict(updated)
    result["send"] = send_meta
    if outcome.tag_conflict:
        result["warning"] = (
            "Reply sent, but the state-tag write hit a 409 (ticket changed "
            "concurrently); the tag was NOT overwritten. Re-tag or re-sync."
        )
    return result


@router.post("/{email_id}/set-status")
async def set_ticket_status_no_reply(
    email_id: str,
    payload: SetStatusRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Set a Zendesk ticket's status (new / open / pending / solved) WITHOUT
    sending a reply.

    For the "no response warranted — just set the state" case: the ticket status
    flips and nothing goes to the requester (no comment). ``solved`` is the common
    "resolve it" action (and the only one with a keyboard shortcut); ``new`` /
    ``open`` re-open or reset the ticket, and ``pending`` parks it awaiting the
    requester. Unlike ``/send`` there is no reply to authorize, so the send gate
    does not apply — but the same closed-ticket guard, audit trail, and
    best-effort re-sync do. A transport failure marks the email ``send_failed``
    (re-triable), mirroring ``/send``.

    Local workflow status: a no-reply ``solved`` is terminal → ``SOLVED``; ``new`` /
    ``open`` / ``pending`` do not resolve the email, so its workflow status is left
    unchanged (only ``zendesk_status`` — and thus the queue bucket — moves).
    """
    payload = payload or SetStatusRequest()
    set_status = payload.status
    email = await email_repo.get_email_by_id(db, email_id)
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email {email_id} not found",
        )

    # No outbound transport for non-Zendesk emails (same as /send).
    if (email.source or "") != EmailSource.ZENDESK.value or not email.zendesk_ticket_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"message": "Status change authorized, but no Zendesk transport "
                    "is configured for this source; nothing changed.",
                    "source": email.source},
        )

    # Closed tickets are immutable (§2) — never attempt a write; report clearly.
    if (email.zendesk_status or "").lower() == "closed":
        await audit_repo.log_action(
            db, email_id, "status_set_blocked_closed", payload.set_by,
            {"zendesk_ticket_id": email.zendesk_ticket_id, "requested_status": set_status},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Zendesk ticket is closed and immutable; cannot "
                    "write. Annotate via a follow-up ticket instead.",
                    "zendesk_status": email.zendesk_status},
        )

    tags = [f"ai_status_{set_status}"]
    updated_stamp = _iso_z(email.zendesk_updated_at)

    try:
        outcome = await zendesk_sender.set_status_only(
            ticket_id=int(email.zendesk_ticket_id),
            status=set_status,
            tags=tags,
            updated_stamp=updated_stamp,
        )
    except ZendeskSendError as exc:
        # Transport failed — record the failure locally so it never reads as
        # done, and keep the email re-triable (mirrors /send).
        send_meta = {
            "state": "failed",
            "public": False,
            "requested_status": set_status,
            "error": str(exc),
            "status_code": exc.status_code,
        }
        failed_draft = {**(email.draft or {}), "send": send_meta}
        await email_repo.update_email_status(
            db, email_id, EmailStatus.SEND_FAILED.value, {"draft": failed_draft}
        )
        await audit_repo.log_action(
            db, email_id, "status_set_failed", payload.set_by, send_meta
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": "Zendesk status write failed; email marked "
                    "send_failed and left re-triable.", "error": str(exc)},
        ) from exc

    # Success — record what happened (no reply). A no-reply "solved" resolves the
    # email locally (SOLVED); "new"/"open" don't, so leave the workflow status be.
    send_meta = {
        "state": "status_set_no_reply",
        "mode": outcome.mode,
        "public": outcome.public,
        "status_set": outcome.status_set,
        "tags_added": outcome.tags_added,
        "tag_conflict": outcome.tag_conflict,
    }
    local_status = (
        EmailStatus.SOLVED.value if set_status == "solved" else email.status
    )
    new_draft = {**(email.draft or {}), "send": send_meta}
    await email_repo.update_email_status(
        db, email_id, local_status, {"draft": new_draft}
    )
    await audit_repo.log_action(
        db, email_id, "status_set_no_reply", payload.set_by, send_meta
    )

    # Reconcile local Zendesk state so the ticket moves buckets immediately
    # (optimistic mirror), then best-effort re-sync for the authoritative state.
    # A re-sync failure must NEVER fail the request — the status is already set
    # in Zendesk.
    try:
        await email_repo.apply_zendesk_fields(
            db, email_id, {"zendesk_status": set_status}
        )
        await zendesk_adapter.refresh_ticket(db, int(email.zendesk_ticket_id))
    except Exception as exc:  # noqa: BLE001 - reconcile is best-effort
        logger.warning(
            "post-status-set reconcile of ticket %s failed (status was set): %s",
            email.zendesk_ticket_id, exc,
        )
        await db.rollback()

    updated = await email_repo.get_email_by_id(db, email_id)
    result = _email_to_dict(updated)
    result["send"] = send_meta
    if outcome.tag_conflict:
        result["warning"] = (
            f"Ticket set to {set_status}, but the state-tag write hit a 409 "
            "(ticket changed concurrently); the tag was NOT overwritten. Re-sync."
        )
    return result


@router.patch("/{email_id}/reroute")
async def reroute_email(
    email_id: str, payload: RerouteRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Reroute an email to a different lane and record the reason."""
    existing = await email_repo.get_email_by_id(db, email_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email {email_id} not found",
        )
    original_lane = (existing.routing or {}).get("lane")
    new_routing = dict(existing.routing or {})
    new_routing["lane"] = payload.new_lane
    updated = await email_repo.update_email_status(
        db, email_id, "rerouted", {"routing": new_routing}
    )
    await audit_repo.log_action(
        db, email_id, "rerouted", payload.rerouted_by,
        {"reason": payload.reason, "new_lane": payload.new_lane},
    )
    # The original lane was wrong → penalize that (intent, lane) arm (no win).
    _record_rl_feedback(existing, original_lane, "rerouted")
    # A reroute involves no draft edit, so only the low-confidence signal applies.
    await _record_flag_events(
        db, email_id, payload.rerouted_by, existing.classification, was_edited=False
    )
    return _email_to_dict(updated)


@router.patch("/{email_id}/reassign-chair")
async def reassign_chair(
    email_id: str, payload: ReassignChairRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Reassign a human-review email to a different chair (Phase 6A).

    Updates ``assigned_chair_id`` and writes a ``chair_reassigned`` audit entry
    through the EXISTING audit mechanism (no new table). The entry captures the
    original + new chair ids and the intent/confidence recorded at assignment
    time (read off the email's stored classification) — the training signal a
    learned chair-routing strategy will later consume to learn from human
    corrections.
    """
    existing = await email_repo.get_email_by_id(db, email_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email {email_id} not found",
        )
    # The target chair must exist (a reassignment can target an inactive chair —
    # that's a deliberate human override — but not a nonexistent one).
    target = await chair_repo.get_chair_by_id(db, payload.new_chair_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chair {payload.new_chair_id} not found",
        )

    original_chair_id = existing.assigned_chair_id
    classification = existing.classification or {}
    updated = await email_repo.assign_chair(db, email_id, payload.new_chair_id)
    await audit_repo.log_action(
        db, email_id, "chair_reassigned", payload.reassigned_by,
        {
            "original_chair_id": original_chair_id,
            "new_chair_id": payload.new_chair_id,
            "reason": payload.reason,
            # Intent + confidence AT ASSIGNMENT TIME (from the stored
            # classification) — the signal a reroute is a correction against.
            "intent": classification.get("intent"),
            "confidence": classification.get("confidence"),
        },
    )
    return _email_to_dict(updated)


async def _would_empty_the_grounding(
    db: AsyncSession,
    email: Email,
    excluded_policy_ids: list[str] | None,
    forced_policy_key: str | None,
) -> bool:
    """Would this request strip the draft of ALL policy context?

    Checked HERE rather than in the pipeline because the re-draft is async: the
    endpoint returns 202 and the real filtering happens in a background task,
    which has no HTTP response left to reject with. Refusing after scheduling
    could only be a silent no-op or a stranded ticket.

    It compares against the email's CURRENT ``retrieval_context.retrieved_ids``
    — the exact cards the chair is looking at and removing from — rather than
    dry-running retrieval. A dry run would re-issue the whole retrieval stage
    (a distiller model call under QUERY_STRATEGY=distill, plus embeddings), and
    being non-deterministic it could disagree with the run that follows.

    A still-standing forced policy rescues an otherwise-empty set: "remove all
    three and ground on this one instead" is the swap workflow this feature
    exists for, and must not be rejected. Lineage roots are resolved with the
    same helper the pipeline filter uses, so the two can never disagree about
    what an exclusion covers.

    Fails OPEN: a legacy row with no stored context has nothing to compare
    against, so the request proceeds (the pipeline degrades safely on its own).
    """
    if not excluded_policy_ids:
        return False
    current = (email.retrieval_context or {}).get("retrieved_ids") or []
    if not current:
        return False

    roots = await resolve_lineage_roots(
        db,
        list(current)
        + list(excluded_policy_ids)
        + ([forced_policy_key] if forced_policy_key else []),
        policy_repo,
    )
    excluded_roots = {roots.get(x, x) for x in excluded_policy_ids}

    if any(roots.get(i, i) not in excluded_roots for i in current):
        return False  # something survives the removal
    # Nothing of the current set survives — only a non-excluded forced pick saves it.
    return not (
        forced_policy_key
        and roots.get(forced_policy_key, forced_policy_key) not in excluded_roots
    )


def _redraft_audit_extra(
    forced_policy_key: str | None, excluded_policy_ids: list[str] | None
) -> dict:
    """Audit payload for a manual re-draft — only the knobs actually used.

    A plain retry stays ``{}`` exactly as before, so existing audit rows and the
    tests asserting on them are unaffected.
    """
    extra: dict = {}
    if forced_policy_key:
        extra["forced_policy_key"] = forced_policy_key
    if excluded_policy_ids:
        extra["excluded_policy_ids"] = excluded_policy_ids
    return extra


async def _redraft_email_bg(
    email_id: str,
    forced_policy_key: str | None = None,
    excluded_policy_ids: list[str] | None = None,
) -> None:
    """Re-run the full pipeline for one email in its OWN session (retry action).

    Scheduled after the endpoint returns (the request's session is closed by
    then). On success the fresh draft overwrites the row and ``redrafting`` is
    cleared by ``reprocess_email``. On failure, clear the flag so the ticket is
    not stranded showing "re-drafting…".

    ``forced_policy_key`` (manual invoke) is passed straight through to the
    pipeline, which grounds on that policy in ADDITION to normal retrieval.
    ``excluded_policy_ids`` (chair removals) likewise rides through to the
    pipeline; the filtering itself lands in a later step.
    """
    pipeline = EmailPipeline()
    try:
        async with async_session_factory() as db:
            email = await email_repo.get_email_by_id(db, email_id)
            if email is None:
                return
            await pipeline.reprocess_email(
                db,
                email,
                forced_policy_key=forced_policy_key,
                excluded_policy_ids=excluded_policy_ids,
            )
            await audit_repo.log_action(
                db,
                email_id,
                "email_retried",
                "chair",
                _redraft_audit_extra(forced_policy_key, excluded_policy_ids),
            )
    except Exception:  # noqa: BLE001 - a failed retry must not crash the worker
        logger.exception("Retry re-draft failed for email %s; clearing flag.", email_id)
        async with async_session_factory() as db:
            await email_repo.set_redrafting(db, email_id, False)


@router.post("/{email_id}/redraft", status_code=status.HTTP_202_ACCEPTED)
async def redraft_email(
    email_id: str,
    background_tasks: BackgroundTasks,
    payload: RedraftRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Retry: re-run the full pipeline on this email and overwrite its draft.

    Marks the ticket ``redrafting`` (surfaced live as the "re-drafting…" badge,
    exactly like a policy-change sweep), then re-classifies → re-retrieves →
    re-routes → re-drafts in the background, clearing the flag when the new draft
    lands. 404 if the email is unknown.

    The body is OPTIONAL: no body (or an empty one) is the unchanged retry. A
    ``forced_policy_key`` grounds the new draft on that policy in addition to
    whatever retrieval ranks — see ``EmailPipeline._force_policy_chunk`` for the
    active-only rule and the ignore-on-miss behaviour. ``excluded_policy_ids``
    are policies the chair removed from the grounding; the value is threaded to
    the pipeline here, and the filter that acts on it lands in a later step.
    """
    email = await email_repo.get_email_by_id(db, email_id)
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email {email_id} not found",
        )
    forced_policy_key = payload.forced_policy_key if payload else None
    excluded_policy_ids = payload.excluded_policy_ids if payload else None

    if await _would_empty_the_grounding(
        db, email, excluded_policy_ids, forced_policy_key
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Removing these policies would leave the draft with no policy "
                "context to work from. Keep at least one policy, or add a "
                "replacement before removing the rest."
            ),
        )

    await email_repo.set_redrafting(db, email_id, True)
    # The audit write publishes an SSE event → queue/detail flip to "re-drafting…".
    await audit_repo.log_action(
        db,
        email_id,
        "email_retry_started",
        "chair",
        _redraft_audit_extra(forced_policy_key, excluded_policy_ids),
    )
    background_tasks.add_task(
        _redraft_email_bg, email_id, forced_policy_key, excluded_policy_ids
    )
    return {
        "email_id": email_id,
        "redrafting": True,
        "forced_policy_key": forced_policy_key,
        "excluded_policy_ids": excluded_policy_ids,
    }
