"""Tests for the live email SSE stream (Phase 5E, Part 1).

Covers the in-process event broker directly and end-to-end: the broker fans an
event out to subscribers, the /stream endpoint opens with the SSE content type
and a connected preamble, and ingesting an email during the test publishes a
lifecycle event to a subscriber. Uses an in-memory SQLite DB via ASGITransport;
no network, no API key (drafter takes its fallback path).
"""

import asyncio

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import main
from app.api.v1.emails import stream_emails
from app.core.events import EventBroker, get_event_broker
from app.db.database import Base, get_db

_SAMPLE_EMAIL = {
    "from": "author@university.edu",
    "to": "chairs@conference.org",
    "subject": "When is the submission deadline?",
    "body": "Could you confirm the full paper submission deadline and timezone?",
    "timestamp": "2026-07-07T09:00:00Z",
}


@pytest_asyncio.fixture
async def client():
    """In-memory DB + an httpx client wired to the app."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_get_db():
        async with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    main.app.dependency_overrides.clear()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Broker unit behavior
# ---------------------------------------------------------------------------
async def test_broker_fans_out_to_subscribers():
    broker = EventBroker()
    q1 = broker.add_subscriber()
    q2 = broker.add_subscriber()
    broker.publish({"action": "classified", "email_id": "1"})

    assert (await asyncio.wait_for(q1.get(), timeout=1))["action"] == "classified"
    assert (await asyncio.wait_for(q2.get(), timeout=1))["action"] == "classified"

    broker.remove_subscriber(q1)
    broker.remove_subscriber(q2)
    assert broker.subscriber_count == 0


def test_broker_publish_with_no_subscribers_is_noop():
    broker = EventBroker()
    # Must not raise even though nobody is listening.
    broker.publish({"action": "routed", "email_id": "5"})
    assert broker.subscriber_count == 0


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
class _FakeRequest:
    """Minimal Request stand-in — the stream handler only calls is_disconnected."""

    async def is_disconnected(self) -> bool:
        return False


async def test_stream_endpoint_opens_with_event_stream():
    # Drive the handler directly: the response body is an infinite generator, so
    # going through httpx's ASGITransport (which buffers) would hang. Reading the
    # first chunk off the body iterator proves the SSE stream opens.
    response = await stream_emails(_FakeRequest())
    assert response.media_type == "text/event-stream"

    iterator = response.body_iterator
    first = await asyncio.wait_for(iterator.__anext__(), timeout=2)
    text = first.decode() if isinstance(first, bytes) else first
    assert "connected" in text
    await iterator.aclose()  # stops the generator + deregisters the subscriber


async def test_ingest_publishes_event_to_subscriber(client):
    # Subscribe directly to the in-process broker, then ingest an email and
    # confirm a lifecycle event was published during processing.
    broker = get_event_broker()
    queue = broker.add_subscriber()
    try:
        resp = await client.post("/api/v1/emails/ingest", json=_SAMPLE_EMAIL)
        assert resp.status_code == 200

        event = await asyncio.wait_for(queue.get(), timeout=2)
        assert "email_id" in event
        assert "action" in event
        # The pipeline's first audit write is the classification stage.
        assert event["action"] == "classified"
    finally:
        broker.remove_subscriber(queue)
