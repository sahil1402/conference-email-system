"""Email API (v1) — ingest, queue, detail, and chair actions.

Thin HTTP layer over the pipeline and repositories. Follows the app's existing
router pattern: a module-level ``router = APIRouter(...)`` mounted by main.py,
and the ``get_db`` dependency from ``app.db.database`` for the async session.
No SQLAlchemy is touched directly here — all persistence goes through the
repositories, all processing through EmailPipeline.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

import logging

from app.db.database import get_db
from app.db.models import AuditLog, Email
from app.pipeline.orchestrator import EmailPipeline
from app.pipeline.rl_router import get_rl_router
from app.repositories.audit_repository import AuditRepository
from app.repositories.email_repository import EmailRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/emails", tags=["emails"])

email_repo = EmailRepository()
audit_repo = AuditRepository()


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
        "classification": email.classification,
        "routing": email.routing,
        "draft": email.draft,
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
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the email queue, optionally filtered by routing lane."""
    emails = await email_repo.get_email_queue(
        db, lane=lane, limit=limit, offset=offset
    )
    counts = await email_repo.count_emails_by_status(db)
    total = sum(counts.values())
    return {
        "emails": [_email_to_dict(e) for e in emails],
        "total": total,
        "page_info": {"limit": limit, "offset": offset},
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
        "email": _email_to_dict(email),
        "audit_trail": [_audit_to_dict(a) for a in trail],
    }


@router.patch("/{email_id}/approve")
async def approve_email(
    email_id: str, payload: ApproveRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Approve an email's draft (optionally with chair-edited final text)."""
    updated = await email_repo.update_email_status(db, email_id, "approved")
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Email {email_id} not found",
        )
    await audit_repo.log_action(
        db, email_id, "approved", payload.approved_by,
        {"final_text": payload.final_text},
    )
    # The approved lane was the right call → reward that (intent, lane) arm.
    _record_rl_feedback(updated, (updated.routing or {}).get("lane"), "approved")
    return _email_to_dict(updated)


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
    return _email_to_dict(updated)
