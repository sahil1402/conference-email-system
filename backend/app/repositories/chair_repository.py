"""Chair persistence (the Chair aggregate of the persistence layer) — Phase 6A.

All access to the ``chairs`` table goes through this repository — the chair
router and API layers never touch SQLAlchemy directly. Reads return ``[]`` /
``None`` on miss rather than raising, matching the other repositories'
no-exception contract.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chair


class ChairRepository:
    """Async data-access methods for the ``chairs`` table."""

    async def get_all_chairs(self, db: AsyncSession) -> list[Chair]:
        """Return every chair, ordered by id."""
        result = await db.execute(select(Chair).order_by(Chair.id))
        return list(result.scalars().all())

    async def get_active_chairs(self, db: AsyncSession) -> list[Chair]:
        """Return only active chairs (the routable roster), ordered by id."""
        result = await db.execute(
            select(Chair).where(Chair.active.is_(True)).order_by(Chair.id)
        )
        return list(result.scalars().all())

    async def get_chair_by_id(
        self, db: AsyncSession, chair_id: int | str
    ) -> Chair | None:
        """Return a single chair by id, or ``None`` if absent / non-numeric."""
        try:
            pk = int(chair_id)
        except (TypeError, ValueError):
            return None
        return await db.get(Chair, pk)
