"""Audit-trail persistence (the AuditLog aggregate of the persistence layer).

Append-only record of actions taken on emails (classification, routing, chair
approvals, sends, reroutes). All access to the ``audit_logs`` table goes through
this repository.

Note: the ORM attribute backing the JSON context column is ``extra_metadata``
(the underlying DB column is literally named ``metadata``, which is reserved on
the declarative base). Callers pass plain ``metadata`` here; it is stored on
``extra_metadata``.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import get_event_broker
from app.db.models import AuditLog


def _coerce_id(email_id: str) -> int | None:
    """Coerce a string email id to the integer FK, or ``None`` if invalid."""
    try:
        return int(email_id)
    except (TypeError, ValueError):
        return None


def _publish_audit_event(entry: AuditLog) -> None:
    """Push a lifecycle event to SSE subscribers (best-effort, never raises).

    Every audit write is a meaningful state change (created / classified /
    routed / drafted / approved / rerouted), so this is the single seam the
    live queue stream listens on.
    """
    get_event_broker().publish(
        {
            "email_id": str(entry.email_id),
            "action": entry.action,
            "actor": entry.actor,
            "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
        }
    )


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
        _publish_audit_event(entry)
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

    @staticmethod
    def _build_filters(
        email_id: str | None,
        action: str | None,
        actor: str | None,
    ) -> list | None:
        """Build a list of SQLAlchemy WHERE conditions from optional filters.

        Returns ``None`` to signal an impossible filter (a non-numeric
        ``email_id``), so callers can short-circuit to an empty result without
        running a query. An empty list means "no filters" (match everything).
        """
        conditions: list = []
        if email_id is not None:
            fk = _coerce_id(email_id)
            if fk is None:
                return None
            conditions.append(AuditLog.email_id == fk)
        if action is not None:
            conditions.append(AuditLog.action == action)
        if actor is not None:
            conditions.append(AuditLog.actor == actor)
        return conditions

    async def get_audit_logs(
        self,
        db: AsyncSession,
        *,
        email_id: str | None = None,
        action: str | None = None,
        actor: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditLog]:
        """Return audit logs with optional filtering and pagination.

        Ordered newest-first by ``timestamp`` (then ``id`` to break ties
        deterministically). A non-numeric ``email_id`` yields an empty list.
        """
        conditions = self._build_filters(email_id, action, actor)
        if conditions is None:
            return []
        stmt = (
            select(AuditLog)
            .where(*conditions)
            .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_audit_log_count(
        self,
        db: AsyncSession,
        *,
        email_id: str | None = None,
        action: str | None = None,
        actor: str | None = None,
    ) -> int:
        """Return the total count matching the same filters (for pagination)."""
        conditions = self._build_filters(email_id, action, actor)
        if conditions is None:
            return 0
        stmt = select(func.count()).select_from(AuditLog).where(*conditions)
        result = await db.execute(stmt)
        return int(result.scalar_one())

    async def get_audit_log_by_id(
        self, db: AsyncSession, log_id: int
    ) -> AuditLog | None:
        """Return a single audit log by primary key, or ``None`` if absent."""
        return await db.get(AuditLog, log_id)

    async def count_reassignments_by_original_chair(
        self, db: AsyncSession
    ) -> dict[int | None, int]:
        """Count ``chair_reassigned`` audit entries grouped by the chair each
        email was moved AWAY from (``original_chair_id`` in the entry metadata).

        A single grouped aggregate over ALL matching audit rows — not a client
        tally over a capped audit page. ``original_chair_id`` is read from the
        JSON metadata column; a ``None`` key means the email had no chair before
        the reassignment (the "Unassigned" bucket).
        """
        # Dialect-agnostic JSON access: SQLAlchemy renders JSON_EXTRACT on
        # SQLite and a ->> cast on PostgreSQL. A bare func.json_extract() is
        # SQLite-only and raises UndefinedFunctionError on Postgres.
        original = AuditLog.extra_metadata["original_chair_id"].as_integer()
        stmt = (
            select(original, func.count(AuditLog.id))
            .where(AuditLog.action == "chair_reassigned")
            .group_by(original)
        )
        result = await db.execute(stmt)
        return {
            (int(chair_id) if chair_id is not None else None): int(count)
            for chair_id, count in result.all()
        }

    async def create_audit_log(
        self,
        db: AsyncSession,
        *,
        email_id: str,
        action: str,
        actor: str,
        details: dict | None = None,
    ) -> AuditLog | None:
        """Insert a new audit log entry and return it.

        ``details`` is stored on the ``extra_metadata`` JSON column. Returns
        ``None`` if ``email_id`` is not a valid integer key (matching the
        repository's no-exception contract).
        """
        fk = _coerce_id(email_id)
        if fk is None:
            return None

        entry = AuditLog(
            email_id=fk,
            action=action,
            actor=actor,
            extra_metadata=details,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        _publish_audit_event(entry)
        return entry
