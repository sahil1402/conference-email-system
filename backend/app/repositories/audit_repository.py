"""Audit-trail persistence (the AuditLog aggregate of the persistence layer).

Append-only record of actions taken on emails (classification, routing, chair
approvals, sends, reroutes). All access to the ``audit_logs`` table goes through
this repository.

Note: the ORM attribute backing the JSON context column is ``extra_metadata``
(the underlying DB column is literally named ``metadata``, which is reserved on
the declarative base). Callers pass plain ``metadata`` here; it is stored on
``extra_metadata``.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


def _coerce_id(email_id: str) -> int | None:
    """Coerce a string email id to the integer FK, or ``None`` if invalid."""
    try:
        return int(email_id)
    except (TypeError, ValueError):
        return None


class AuditRepository:
    """Async data-access methods for the ``audit_logs`` table."""

    async def log_action(
        self,
        db: AsyncSession,
        email_id: str,
        action: str,
        actor: str,
        metadata: dict = {},
    ) -> AuditLog | None:
        """Append an audit entry for an email and return it.

        Returns ``None`` if ``email_id`` is not a valid integer key (rather than
        raising), keeping the repository's no-exception contract.
        """
        fk = _coerce_id(email_id)
        if fk is None:
            return None

        entry = AuditLog(
            email_id=fk,
            action=action,
            actor=actor,
            extra_metadata=metadata,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return entry

    async def get_audit_trail(
        self, db: AsyncSession, email_id: str
    ) -> list[AuditLog]:
        """Return all audit entries for an email in chronological order."""
        fk = _coerce_id(email_id)
        if fk is None:
            return []
        result = await db.execute(
            select(AuditLog)
            .where(AuditLog.email_id == fk)
            .order_by(AuditLog.timestamp.asc(), AuditLog.id.asc())
        )
        return list(result.scalars().all())

    async def get_recent_actions(
        self, db: AsyncSession, limit: int = 20
    ) -> list[AuditLog]:
        """Return the most recent audit entries across all emails."""
        result = await db.execute(
            select(AuditLog)
            .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
