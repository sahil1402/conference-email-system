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

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Email
from app.models.enums import EmailStatus


def _queue_conditions(
    lane: str | None,
    chair_id: int | None,
    status: str | None,
    search: str | None,
    unassigned: bool,
) -> list:
    """Build the shared WHERE conditions for the queue list AND its count.

    Kept in one place so ``get_email_queue`` and ``count_email_queue`` filter
    identically — the page and the total can never disagree. All filters are
    server-side: the lane lives in the ``routing`` JSON column, ``chair_id`` /
    ``unassigned`` on the ``assigned_chair_id`` FK, ``status`` on the column, and
    ``search`` is a case-insensitive match on subject OR sender.
    """
    conditions: list = []
    if lane is not None:
        # Dialect-agnostic JSON access: SQLAlchemy renders JSON_EXTRACT on
        # SQLite and the ->> / #>> operators on PostgreSQL. A bare
        # func.json_extract() is SQLite-only and raises UndefinedFunctionError
        # on Postgres.
        conditions.append(Email.routing["lane"].as_string() == lane)
    if chair_id is not None:
        conditions.append(Email.assigned_chair_id == chair_id)
    if unassigned:
        conditions.append(Email.assigned_chair_id.is_(None))
    if status is not None:
        conditions.append(Email.status == status)
    if search:
        pattern = f"%{search}%"
        conditions.append(
            or_(Email.subject.ilike(pattern), Email.sender.ilike(pattern))
        )
    return conditions


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

    async def claim_for_redraft(self, db: AsyncSession, email_id: str) -> bool:
        """Atomically claim an open ticket for re-drafting.

        A single conditional UPDATE flips ``redrafting`` False→True only while the
        ticket is still an open auto-draft (status draft_generated) and not already
        being re-drafted. Returns True iff THIS call won the claim. Because the flip
        is one SQL statement, two overlapping sweeps can never both claim the same
        ticket, and a ticket approved since the sweep started is not claimed.
        """
        pk = _coerce_id(email_id)
        if pk is None:
            return False
        result = await db.execute(
            update(Email)
            .where(
                Email.id == pk,
                Email.status == EmailStatus.DRAFT_GENERATED.value,
                Email.redrafting.is_(False),
            )
            .values(redrafting=True)
        )
        await db.commit()
        return (result.rowcount or 0) == 1

    async def set_redrafting(
        self, db: AsyncSession, email_id: str, value: bool
    ) -> Email | None:
        """Set/clear the transient ``redrafting`` flag unconditionally.

        A Core UPDATE (not load-set-commit) so it always writes even when the
        session's cached instance is stale — otherwise clearing a flag set by a
        prior Core UPDATE in the same session would be a silent no-op. Returns the
        refreshed row, or None if the id is missing/non-numeric.
        """
        pk = _coerce_id(email_id)
        if pk is None:
            return None
        result = await db.execute(
            update(Email).where(Email.id == pk).values(redrafting=value)
        )
        await db.commit()
        if (result.rowcount or 0) != 1:
            return None
        refreshed = await db.execute(
            select(Email).where(Email.id == pk).execution_options(populate_existing=True)
        )
        return refreshed.scalar_one_or_none()

    async def save_redraft(
        self,
        db: AsyncSession,
        email_id: str,
        *,
        draft: dict,
        routing: dict,
        retrieval_context: dict,
    ) -> Email | None:
        """Persist a re-drafted ticket IFF it is still a claimed open draft.

        A single conditional UPDATE overwrites draft + routing + retrieval_context
        and clears ``redrafting`` only while status is still draft_generated AND the
        ticket is still claimed (``redrafting`` True). If the chair approved/changed
        the ticket between claim and save, 0 rows match and this returns ``None``
        WITHOUT clobbering their work (the caller then clears the stray flag).
        Status is left unchanged. Returns ``None`` if the id is missing/non-numeric
        or the precondition no longer holds.
        """
        pk = _coerce_id(email_id)
        if pk is None:
            return None
        result = await db.execute(
            update(Email)
            .where(
                Email.id == pk,
                Email.status == EmailStatus.DRAFT_GENERATED.value,
                Email.redrafting.is_(True),
            )
            .values(
                draft=draft,
                routing=routing,
                retrieval_context=retrieval_context,
                redrafting=False,
            )
        )
        await db.commit()
        if (result.rowcount or 0) != 1:
            return None
        refreshed = await db.execute(
            select(Email).where(Email.id == pk).execution_options(populate_existing=True)
        )
        return refreshed.scalar_one_or_none()

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
        chair_id: int | None = None,
        status: str | None = None,
        search: str | None = None,
        unassigned: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Email]:
        """Return the email queue, filtered server-side by any combination of
        lane / chair / unassigned / status / search.

        Every filter is applied in SQL (via :func:`_queue_conditions`), so the
        returned page is a slice of the FULL matching set — callers never filter
        a truncated page client-side. Ordered newest first.
        """
        conditions = _queue_conditions(lane, chair_id, status, search, unassigned)
        stmt = (
            select(Email)
            .where(*conditions)
            .order_by(Email.received_at.desc(), Email.id.desc())
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

    async def count_email_queue(
        self,
        db: AsyncSession,
        lane: str | None = None,
        chair_id: int | None = None,
        status: str | None = None,
        search: str | None = None,
        unassigned: bool = False,
    ) -> int:
        """Return the total number of emails matching the queue filters.

        Uses the SAME :func:`_queue_conditions` as ``get_email_queue`` so the
        count and the page always agree. With no filters this is the whole table;
        with filters it is that slice's true total — page-size independent, so
        callers can show an accurate count regardless of ``limit``/``offset``.
        """
        conditions = _queue_conditions(lane, chair_id, status, search, unassigned)
        stmt = select(func.count()).select_from(Email).where(*conditions)
        result = await db.execute(stmt)
        return int(result.scalar_one())

    async def get_open_tickets(self, db: AsyncSession) -> list[Email]:
        """Return every open ticket (status draft_generated), oldest id first.

        "Open" = has an auto-draft awaiting chair action and not yet approved or
        sent. These are the only tickets a KB-change sweep re-evaluates.
        """
        result = await db.execute(
            select(Email)
            .where(Email.status == EmailStatus.DRAFT_GENERATED.value)
            .order_by(Email.id)
        )
        return list(result.scalars().all())

    async def count_by_chair(self, db: AsyncSession) -> dict[int, int]:
        """Return a mapping of ``assigned_chair_id`` -> count over ALL emails.

        A single grouped aggregate (not a paginated scan) so per-chair volume is
        accurate regardless of how many emails exist or how they are ordered.
        Only rows with a chair assigned are counted (NULL FKs are excluded).
        """
        stmt = (
            select(Email.assigned_chair_id, func.count(Email.id))
            .where(Email.assigned_chair_id.is_not(None))
            .group_by(Email.assigned_chair_id)
        )
        result = await db.execute(stmt)
        return {int(chair_id): int(count) for chair_id, count in result.all()}
