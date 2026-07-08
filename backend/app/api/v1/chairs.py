"""Chairs API (v1) — read-only roster for the frontend (Phase 6B support).

Mounted under ``/api/v1``, so the public path is ``/api/v1/chairs``. Exposes the
seeded chair roster so the UI can resolve an email's ``assigned_chair_id`` to a
name and populate the reassignment picker. Read-only: chairs are created/edited
via the migration seed, not the API.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import Chair
from app.repositories.chair_repository import ChairRepository

router = APIRouter(prefix="/chairs", tags=["chairs"])

chair_repo = ChairRepository()


def _chair_to_dict(chair: Chair) -> dict:
    return {
        "id": chair.id,
        "name": chair.name,
        "role_title": chair.role_title,
        "areas": list(chair.areas or []),
        "active": chair.active,
    }


@router.get("")
async def list_chairs(
    active_only: bool = False, db: AsyncSession = Depends(get_db)
) -> dict:
    """Return the chair roster (all chairs by default; active only when asked)."""
    chairs = (
        await chair_repo.get_active_chairs(db)
        if active_only
        else await chair_repo.get_all_chairs(db)
    )
    return {"chairs": [_chair_to_dict(c) for c in chairs], "total": len(chairs)}
