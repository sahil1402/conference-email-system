"""Tests for the Zendesk sync overlap guard (single-flight lock).

Hermetic: in-memory async SQLite (built via create_all, so the new lock columns
exist without applying the migration) and fake HTTP — nothing hits real Zendesk.
Covers: a second trigger is refused while one runs; a stale/crashed lock is
reclaimed; release frees it; and the adapter skips (does no work) when locked.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.integrations.zendesk import adapter as adapter_mod
from app.integrations.zendesk.adapter import ZendeskIngestAdapter
from app.repositories.zendesk_repository import ZendeskSyncStateRepository

SUB = "testsub"
STALE = 900


@pytest_asyncio.fixture
async def adb():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _nosleep(*_a, **_k):
    return None


class FakeProvider:
    base_url = "https://aaai.zendesk.com/api/v2"

    def get_auth_header(self):
        return {"Authorization": "Bearer test"}


class FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.headers = {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class EmptyPageClient:
    """Returns one empty incremental page (end_of_stream) — a no-op cycle."""

    def __init__(self):
        self.get_calls = 0

    async def get(self, url, params=None, headers=None):
        self.get_calls += 1
        return FakeResponse(
            {"tickets": [], "users": [], "after_cursor": "C1", "end_of_stream": True}
        )

    async def aclose(self):
        return None


class ExplodingClient:
    """Any HTTP use is a test failure — proves a skipped sync did no work."""

    def __init__(self):
        self.get_calls = 0

    async def get(self, *a, **k):
        self.get_calls += 1
        raise AssertionError("HTTP GET must not happen when the sync is skipped")

    async def aclose(self):
        return None


# === repository-level lock behavior =======================================


@pytest.mark.asyncio
async def test_second_acquire_rejected_while_running(adb):
    repo = ZendeskSyncStateRepository()
    await repo.get_or_create(adb, SUB, 1)

    assert await repo.try_acquire_lock(adb, SUB, stale_after_seconds=STALE) is True
    # A second acquire while the first holds it (fresh timestamp) is refused.
    assert await repo.try_acquire_lock(adb, SUB, stale_after_seconds=STALE) is False


@pytest.mark.asyncio
async def test_stale_lock_allows_takeover(adb):
    repo = ZendeskSyncStateRepository()
    state = await repo.get_or_create(adb, SUB, 1)
    # Simulate a crashed run: is_running True but running_since long ago.
    state.is_running = True
    state.running_since = datetime.now(timezone.utc) - timedelta(seconds=STALE + 100)
    await adb.commit()

    # Older than the stale window → a new cycle reclaims it.
    assert await repo.try_acquire_lock(adb, SUB, stale_after_seconds=STALE) is True


@pytest.mark.asyncio
async def test_release_then_reacquire(adb):
    repo = ZendeskSyncStateRepository()
    await repo.get_or_create(adb, SUB, 1)
    assert await repo.try_acquire_lock(adb, SUB, stale_after_seconds=STALE) is True
    await repo.release_lock(adb, SUB)
    # After release, it's free again.
    assert await repo.try_acquire_lock(adb, SUB, stale_after_seconds=STALE) is True


# === adapter-level behavior ================================================


@pytest.mark.asyncio
async def test_sync_skips_when_lock_held(adb, monkeypatch):
    monkeypatch.setattr(adapter_mod.settings, "ZENDESK_SUBDOMAIN", SUB)
    repo = ZendeskSyncStateRepository()
    await repo.get_or_create(adb, SUB, 1)
    # Another cycle holds the lock (fresh).
    assert await repo.try_acquire_lock(adb, SUB, stale_after_seconds=STALE) is True

    adapter = ZendeskIngestAdapter(provider=FakeProvider(), state_repo=repo)
    client = ExplodingClient()
    res = await adapter.sync(adb, client=client, sleep=_nosleep)

    assert res.skipped is True
    assert res.skipped_reason
    assert res.created == 0 and res.tickets_seen == 0
    assert client.get_calls == 0  # no HTTP work happened


@pytest.mark.asyncio
async def test_sync_acquires_and_releases_lock(adb, monkeypatch):
    monkeypatch.setattr(adapter_mod.settings, "ZENDESK_SUBDOMAIN", SUB)
    repo = ZendeskSyncStateRepository()
    adapter = ZendeskIngestAdapter(provider=FakeProvider(), state_repo=repo)

    res = await adapter.sync(adb, client=EmptyPageClient(), sleep=_nosleep)
    assert res.skipped is False

    # Lock is released after a normal cycle, so the next trigger can proceed.
    state = await repo.get_or_create(adb, SUB, 1)
    assert state.is_running is False
    assert await repo.try_acquire_lock(adb, SUB, stale_after_seconds=STALE) is True
