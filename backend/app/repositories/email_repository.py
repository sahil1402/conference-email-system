"""Email persistence (the Email aggregate of the persistence layer).

All access to the `emails` table goes through this repository — the pipeline
and API layers never touch SQLAlchemy directly. Every method is async and uses
the 2.0-style `select()` API. Reads return ``None`` / ``[]`` on miss rather
than raising; writes commit then refresh so callers get a live, populated row.

Note on ids: the ``Email`` primary key is an integer (autoincrement). The
method signatures accept ``str`` (matching the API/spec contract, where ids
arrive as path/query strings) and coerce internally; a non-numeric id resolves
to "not found" rather than an error.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Email


def _coerce_id(email_id: str) -> int | None:
    """Best-effort coercion of a string id to the integer PK.

    Returns ``None`` when the value cannot be an integer key, so callers can
    treat it as a clean not-found instead of raising.
    """
    try:
        return int(email_id)
    except (TypeError, ValueError):
        return None


class EmailRepository:
    """Async data-access methods for the ``emails`` table."""

    # --- writes -----------------------------------------------------------
    async def create_email(self, db: AsyncSession, email_data: dict) -> Email:
        """Insert a new email row and return the persisted instance."""
        email = Email(**email_data)
        db.add(email)
        await db.commit()
        await db.refresh(email)
        return email

    async def update_email_status(
        self,
        db: AsyncSession,
        email_id: str,
        status: str,
        metadata: dict = {},
    ) -> Email | None:
        """Update an email's status (and optional pipeline-output columns).

        ``metadata`` keys that match real ``Email`` columns (e.g.
        ``classification``, ``routing``, ``draft``) are applied as a convenience
        so a status transition and its produced artifact can be persisted in one
        call. Unknown keys are ignored. Returns ``None`` if the email is absent.
        """
        pk = _coerce_id(email_id)
        if pk is None:
            return None

        result = await db.execute(select(Email).where(Email.id == pk))
        email = result.scalar_one_or_none()
        if email is None:
            return None

        email.status = status
        for key, value in metadata.items():
            if key in {"classification", "routing", "draft"}:
                setattr(email, key, value)

        await db.commit()
        await db.refresh(email)
        return email

    async def assign_chair(
        self, db: AsyncSession, email_id: str, chair_id: int | None
    ) -> Email | None:
        """Set an email's ``assigned_chair_id`` (a chair (re)assignment).

        Kept separate from ``update_email_status`` because a chair reassignment
        is not a lifecycle-status change — the email stays in the human-review
        lane, only its owning chair changes. Returns ``None`` if the email is
        absent or the id is non-numeric.
        """
        pk = _coerce_id(email_id)
        if pk is None:
            return None
        result = await db.execute(select(Email).where(Email.id == pk))
        email = result.scalar_one_or_none()
        if email is None:
            return None
        email.assigned_chair_id = chair_id
        await db.commit()
        await db.refresh(email)
        return email

    # --- reads ------------------------------------------------------------
    async def get_email_by_id(
        self, db: AsyncSession, email_id: str
    ) -> Email | None:
        """Return a single email by id, or ``None`` if not found."""
        pk = _coerce_id(email_id)
        if pk is None:
            return None
        result = await db.execute(select(Email).where(Email.id == pk))
        return result.scalar_one_or_none()

    async def get_emails_by_status(
        self,
        db: AsyncSession,
        status: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Email]:
        """Return emails in a given status, newest first, paginated."""
        result = await db.execute(
            select(Email)
            .where(Email.status == status)
            .order_by(Email.received_at.desc(), Email.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_email_queue(
        self,
        db: AsyncSession,
        lane: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Email]:
        """Return the email queue, optionally filtered by routing lane.

        The lane lives inside the ``routing`` JSON column (``RoutingDecision.lane``),
        so filtering uses a JSON path extraction. When ``lane`` is ``None`` the
        full queue is returned. Ordered newest first.
        """
        stmt = select(Email)
        if lane is not None:
            stmt = stmt.where(func.json_extract(Email.routing, "$.lane") == lane)
        stmt = (
            stmt.order_by(Email.received_at.desc(), Email.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def count_emails_by_status(self, db: AsyncSession) -> dict[str, int]:
        """Return a mapping of status -> count across all emails."""
        result = await db.execute(
            select(Email.status, func.count(Email.id)).group_by(Email.status)
        )
        return {status: count for status, count in result.all()}
