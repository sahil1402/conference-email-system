"""Zendesk sync-state persistence (poller checkpoint).

All access to the ``zendesk_sync_state`` table goes through this repository, so
the ingest adapter never touches SQLAlchemy directly (same rule as the other
repositories). One logical row per Zendesk account (keyed by subdomain) holds
the incremental-export resume cursor and light bookkeeping.
"""

from datetime import datetime

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
