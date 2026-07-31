"""Append-only persistence for CEL suggestion review actions.

``policy_suggestions.reviewed_by`` / ``reviewed_reason`` are current-state fields
that every review action overwrites. This repository writes the history they
lose: one immutable row per accept/reject. There is no update or delete method
by design — the table is append-only.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SuggestionAuditLog


class SuggestionAuditRepository:
    """Async writer for the ``suggestion_audit_logs`` table."""

    async def log(
        self,
        db: AsyncSession,
        *,
        suggestion_id: int,
        action: str,
        actor: str,
        reason: str | None = None,
        resulting_policy_key: str | None = None,
        details: dict | None = None,
    ) -> SuggestionAuditLog:
        """Append one review entry and return it.

        Keyword-only after ``db`` (as ``PolicyAuditRepository.log`` is): ``action``
        and ``actor`` are both plain strings and would transpose silently.

        Commits on its own, the same two-commit shape as ``create_policy`` →
        ``PolicyAuditRepository.log``. Callers are expected to ``await`` this
        UNGUARDED: a failed governance write should surface, not be swallowed.
        """
        entry = SuggestionAuditLog(
            suggestion_id=suggestion_id,
            action=action,
            actor=actor,
            reason=reason,
            resulting_policy_key=resulting_policy_key,
            details=details,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return entry
