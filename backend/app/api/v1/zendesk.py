"""Zendesk API (v1) — manual sync trigger.

Thin HTTP layer over the ingest adapter. The endpoint calls the SAME
``run_sync_cycle`` the background poll loop uses, so on-demand and scheduled
polling share one code path (and a future webhook is just another caller).
Read-only: this triggers a pull cycle, never a write back to Zendesk.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import parse_zendesk_statuses
from app.db.database import get_db
from app.integrations.zendesk.adapter import run_sync_cycle

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/zendesk", tags=["zendesk"])


@router.post("/sync")
async def sync_zendesk(
    db: AsyncSession = Depends(get_db),
    max_pages: int | None = Query(
        None, ge=1, description="Cap pages this cycle (default: configured limit)."
    ),
    per_page: int | None = Query(
        None,
        ge=1,
        le=1000,
        description="Tickets per page (default: configured per-page). Zendesk caps at 1000.",
    ),
    statuses: str | None = Query(
        None,
        description="Comma-separated status allow-list override for THIS call only "
        "(e.g. 'open,pending'). Same format/validation as ZENDESK_SYNC_STATUSES; "
        "unknown tokens are ignored and an empty/all-invalid value falls back to "
        "all statuses. Omit to use the configured default.",
    ),
) -> dict:
    """Trigger one Zendesk polling cycle on demand and return its counts.

    ``max_pages``/``per_page`` bound the cycle for controlled HTTP-triggered test
    runs (Piece 4 follow-up); ``statuses`` overrides the ingest status allow-list
    for this call only. All are optional and fall back to config defaults.
    """
    # Parse the override with the SAME helper the config default uses, so both
    # apply identical rules. Only a real string is parsed; when the param is
    # omitted (None) the adapter falls back to the configured default. (The
    # isinstance guard also tolerates direct in-process calls, where an unset
    # Query default arrives as a sentinel rather than None.)
    parsed_statuses = (
        parse_zendesk_statuses(statuses) if isinstance(statuses, str) else None
    )
    try:
        result = await run_sync_cycle(
            db, max_pages=max_pages, per_page=per_page, statuses=parsed_statuses
        )
    except Exception as exc:  # noqa: BLE001 - surface an adapter failure as 502
        logger.exception("Manual Zendesk sync failed.")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Zendesk sync failed: {exc}",
        ) from exc
    # Overlap guard: another cycle already running → clear 409, no work done.
    if result.skipped:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "A Zendesk sync is already in progress; this trigger was skipped.",
                "reason": result.skipped_reason,
            },
        )
    return result.model_dump()
