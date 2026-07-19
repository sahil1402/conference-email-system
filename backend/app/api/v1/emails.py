"""Email API (v1) — ingest, queue, detail, and chair actions.

Thin HTTP layer over the pipeline and repositories. Follows the app's existing
router pattern: a module-level ``router = APIRouter(...)`` mounted by main.py,
and the ``get_db`` dependency from ``app.db.database`` for the async session.
No SQLAlchemy is touched directly here — all persistence goes through the
repositories, all processing through EmailPipeline.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import get_event_broker
from app.core.send_gate import authorize_send
from app.core.tracing import read_traces
from app.db.database import get_db
from app.pipeline.active_learning import build_flag_events
from app.db.models import AuditLog, Email
from app.pipeline.drafter import find_placeholders
from app.pipeline.orchestrator import EmailPipeline
from app.pipeline.rl_router import get_rl_router
from app.repositories.audit_repository import AuditRepository
from app.repositories.chair_repository import ChairRepository
from app.repositories.email_repository import EmailRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/emails", tags=["emails"])

email_repo = EmailRepository()
audit_repo = AuditRepository()
chair_repo = ChairRepository()


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
        "classification": email.classification,
        "routing": email.routing,
        "draft": email.draft,
        "redrafting": bool(email.redrafting),
        "retrieval_context": email.retrieval_context,
        "created_at": email.created_at.isoformat() if email.created_at else None,
        "updated_at": email.updated_at.isoformat() if email.updated_at else None,
    }


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


@router.get("/queue")
async def get_queue(
    lane: str | None = None,
    chair_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    unassigned: bool = False,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the email queue, filtered server-side by any combination of
    lane / chair / unassigned / status / search.

    ``total`` is the count for the SAME filter set (not the whole table), so a
    scoped caller gets an accurate total independent of ``limit``/``offset`` and
    the returned rows are the full server-side slice — callers never filter or
    count a truncated page client-side.
    """
    kwargs = dict(
        lane=lane,
        chair_id=chair_id,
        status=status,
        search=search,
        unassigned=unassigned,
    )
    emails = await email_repo.get_email_queue(db, limit=limit, offset=offset, **kwargs)
    total = await email_repo.count_email_queue(db, **kwargs)
    return {
        "emails": [_email_to_dict(e) for e in emails],
        "total": total,
        "page_info": {"limit": limit, "offset": offset, **kwargs},
    }


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
        "email": _email_to_dict(email),
        "audit_trail": [_audit_to_dict(a) for a in trail],
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


@router.patch("/{email_id}/approve")
async def approve_email(
    email_id: str, payload: ApproveRequest, db: AsyncSession = Depends(get_db)
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
    return _email_to_dict(updated)


@router.post("/{email_id}/send")
async def send_email_reply(
    email_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    """Release an email's draft to the outbound transport — gate enforced.

    This is the ONLY path a transport may ever hang off: the send gate
    (app/core/send_gate.py) decides first, and both outcomes are audited.
    With no transport configured yet, an authorized send answers 501 and the
    draft stays queued — the gate contract is live before the transport is.
    """
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

    # Authorized — but no outbound transport exists yet (Zendesk write-back
    # will plug in here). The draft remains queued; status is unchanged.
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={"message": "Send authorized, but no outbound transport is "
                "configured; the draft remains queued.",
                "mode": decision.mode},
    )


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
