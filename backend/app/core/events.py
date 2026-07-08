"""In-process event broker for live UI updates (Phase 5E).

A tiny publish/subscribe hub used to push email lifecycle events to connected
Server-Sent-Events clients. Deliberately in-process and dependency-free — no
Redis, no message broker — which is all that is needed at MVP scale (a single
API process).

Every meaningful state change is already recorded as an audit-log write, so the
audit repository publishes here from its write path; the ``/api/v1/emails/stream``
endpoint subscribes. Publishing is non-blocking and best-effort: a slow or full
subscriber queue drops events rather than blocking the pipeline, and a broker
with no subscribers is a no-op.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# Per-subscriber buffer size. If a client falls this far behind, further events
# are dropped for it (it will still recover on the next poll / reconnect).
_SUBSCRIBER_MAXSIZE = 100


class EventBroker:
    """Fan-out of email lifecycle events to per-connection async queues."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def add_subscriber(self) -> asyncio.Queue:
        """Register a new subscriber and return its event queue."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=_SUBSCRIBER_MAXSIZE)
        self._subscribers.add(queue)
        return queue

    def remove_subscriber(self, queue: asyncio.Queue) -> None:
        """Deregister a subscriber (on disconnect)."""
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: dict) -> None:
        """Fan an event out to every subscriber. Never blocks, never raises.

        Full queues (slow consumers) simply drop the event — losing a push is
        harmless because the queue UI also polls as a fallback.
        """
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("Dropping event for a full subscriber queue.")
            except Exception:  # noqa: BLE001 - publishing must never break callers
                logger.debug("Failed to enqueue event for a subscriber.", exc_info=True)


# Process-wide singleton.
_broker = EventBroker()


def get_event_broker() -> EventBroker:
    """Return the process-wide event broker singleton."""
    return _broker
