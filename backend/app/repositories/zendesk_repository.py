"""Zendesk sync-state persistence (poller checkpoint).

All access to the ``zendesk_sync_state`` table goes through this repository, so
the ingest adapter never touches SQLAlchemy directly (same rule as the other
repositories). One logical row per Zendesk account (keyed by subdomain) holds
the incremental-export resume cursor and light bookkeeping.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ZendeskSyncState


class ZendeskSyncStateRepository:
    """Async data-access for the poller's checkpoint row."""

    async def get_or_create(
        self, db: AsyncSession, subdomain: str, default_start_time: int | None
    ) -> ZendeskSyncState:
        """Return the checkpoint row for ``subdomain``, creating it if absent."""
        result = await db.execute(
            select(ZendeskSyncState).where(ZendeskSyncState.subdomain == subdomain)
        )
        state = result.scalar_one_or_none()
        if state is not None:
            return state
        state = ZendeskSyncState(
            subdomain=subdomain, start_time=default_start_time, tickets_seen=0
        )
        db.add(state)
        await db.commit()
        await db.refresh(state)
        return state

    async def update_state(
        self,
        db: AsyncSession,
        state: ZendeskSyncState,
        *,
        cursor: str | None = None,
        set_cursor: bool = False,
        last_synced_at: datetime | None = None,
        last_error: str | None = None,
        set_last_error: bool = False,
        add_seen: int = 0,
    ) -> ZendeskSyncState:
        """Persist checkpoint updates and commit.

        Explicit ``set_*`` flags distinguish "set this to None" from "leave it
        alone" (e.g. clearing ``last_error`` on success vs not touching it).
        ``add_seen`` increments the cumulative ticket counter.
        """
        if set_cursor:
            state.cursor = cursor
        if last_synced_at is not None:
            state.last_synced_at = last_synced_at
        if set_last_error:
            state.last_error = last_error
        if add_seen:
            state.tickets_seen = (state.tickets_seen or 0) + add_seen
        await db.commit()
        await db.refresh(state)
        return state

    async def try_acquire_lock(
        self,
        db: AsyncSession,
        subdomain: str,
        *,
        stale_after_seconds: int,
        now: datetime | None = None,
    ) -> bool:
        """Atomically claim the single-flight sync lock for ``subdomain``.

        Returns True if the lock was acquired (and stamps ``is_running`` +
        ``running_since``), False if a live cycle already holds it. The row is
        selected ``FOR UPDATE`` so two concurrent acquirers serialize on
        PostgreSQL — the first wins, the second reads ``is_running=True`` and is
        refused (``FOR UPDATE`` is a harmless no-op on SQLite, which is
        single-writer anyway). A claim whose ``running_since`` is older than
        ``stale_after_seconds`` is treated as a crashed run and reclaimed, so a
        mid-cycle crash can never lock out future syncs permanently.
        """
        now = now or datetime.now(timezone.utc)
        result = await db.execute(
            select(ZendeskSyncState)
            .where(ZendeskSyncState.subdomain == subdomain)
            .with_for_update()
        )
        state = result.scalar_one_or_none()
        if state is None:
            # No checkpoint row yet — callers create it via get_or_create first.
            await db.rollback()
            return False

        if state.is_running:
            since = state.running_since
            if since is not None:
                if since.tzinfo is None:
                    since = since.replace(tzinfo=timezone.utc)
                age = (now - since).total_seconds()
                if age < stale_after_seconds:
                    # A live cycle holds the lock — refuse and release the row.
                    await db.rollback()
                    return False
            # is_running with a stale/absent timestamp → crashed; reclaim it.

        state.is_running = True
        state.running_since = now
        await db.commit()
        return True

    async def release_lock(self, db: AsyncSession, subdomain: str) -> None:
        """Release the sync lock for ``subdomain`` (idempotent, resilient).

        Rolls back any half-open transaction first so the release can still
        commit even if the cycle failed mid-flight, then clears ``is_running``.
        """
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001 - releasing must not raise
            pass
        result = await db.execute(
            select(ZendeskSyncState).where(ZendeskSyncState.subdomain == subdomain)
        )
        state = result.scalar_one_or_none()
        if state is not None:
            state.is_running = False
            state.running_since = None
            await db.commit()
