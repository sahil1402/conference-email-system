"""Audit log routes.

Read access to the append-only ``audit_logs`` table: a paginated, filterable
feed of every action taken on an email (classification, routing, drafting,
chair approvals, reroutes), plus single-entry lookup.

Mounted under ``/api/v1`` by main.py, so the public paths are
``/api/v1/audit`` and ``/api/v1/audit/{log_id}``. All persistence goes through
``AuditRepository`` — no SQLAlchemy is touched directly here.

Schema note: the ORM stores the timestamp on ``AuditLog.timestamp`` and
free-form context on ``AuditLog.extra_metadata`` (DB column ``metadata``). The
API surfaces these as ``created_at`` and ``details`` respectively.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.repositories.audit_repository import AuditRepository

router = APIRouter(prefix="/audit", tags=["audit"])

audit_repo = AuditRepository()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class AuditLogResponse(BaseModel):
    """A single audit log entry as exposed over the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email_id: int
    action: str
    actor: str
    details: dict | None = Field(
        default=None,
        validation_alias="extra_metadata",
        description="Free-form structured context (stored on the metadata column).",
    )
    created_at: datetime | None = Field(
        default=None,
        validation_alias="timestamp",
        description="When the action was recorded.",
    )


class AuditLogPage(BaseModel):
    """A page of audit log entries with pagination metadata."""

    items: list[AuditLogResponse]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("", response_model=AuditLogPage)
async def list_audit_logs(
    email_id: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> AuditLogPage:
    """Return a paginated, optionally-filtered feed of audit log entries.

    Filters are ANDed together. ``total`` reflects the full count matching the
    same filters (ignoring pagination), so the client can page through it.
    """
    logs = await audit_repo.get_audit_logs(
        db,
        email_id=email_id,
        action=action,
        actor=actor,
        limit=limit,
        offset=offset,
    )
    total = await audit_repo.get_audit_log_count(
        db, email_id=email_id, action=action, actor=actor
    )
    return AuditLogPage(
        items=[AuditLogResponse.model_validate(log) for log in logs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{log_id}", response_model=AuditLogResponse)
async def get_audit_log(
    log_id: int, db: AsyncSession = Depends(get_db)
) -> AuditLogResponse:
    """Return a single audit log entry by id, or 404 if it does not exist."""
    log = await audit_repo.get_audit_log_by_id(db, log_id)
    if log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audit log {log_id} not found",
        )
    return AuditLogResponse.model_validate(log)
