"""Append-only persistence for KB governance actions (policy_audit_logs)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PolicyAuditLog


class PolicyAuditRepository:
    """Async writer for the ``policy_audit_logs`` table."""

    async def log(
        self,
        db: AsyncSession,
        *,
        policy_key: str,
        action: str,
        actor: str,
        before: dict | None = None,
        after: dict | None = None,
    ) -> PolicyAuditLog:
        """Append one governance entry and return it."""
        entry = PolicyAuditLog(
            policy_key=policy_key, action=action, actor=actor, before=before, after=after
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return entry

    async def list(
        self, db: AsyncSession, *, limit: int = 200, offset: int = 0
    ) -> list[PolicyAuditLog]:
        """Return governance history, newest first."""
        stmt = (
            select(PolicyAuditLog)
            .order_by(PolicyAuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await db.execute(stmt)).scalars().all())
