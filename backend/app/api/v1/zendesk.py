"""Zendesk API (v1) — manual sync trigger.

Thin HTTP layer over the ingest adapter. The endpoint calls the SAME
``run_sync_cycle`` the background poll loop uses, so on-demand and scheduled
polling share one code path (and a future webhook is just another caller).
Read-only: this triggers a pull cycle, never a write back to Zendesk.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.integrations.zendesk.adapter import run_sync_cycle

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/zendesk", tags=["zendesk"])


@router.post("/sync")
async def sync_zendesk(db: AsyncSession = Depends(get_db)) -> dict:
    """Trigger one Zendesk polling cycle on demand and return its counts."""
    try:
        result = await run_sync_cycle(db)
    except Exception as exc:  # noqa: BLE001 - surface an adapter failure as 502
        logger.exception("Manual Zendesk sync failed.")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Zendesk sync failed: {exc}",
        ) from exc
    return result.model_dump()
