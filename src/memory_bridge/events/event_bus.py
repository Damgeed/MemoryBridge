"""Redis-backed event bus for real-time memory sync.

Publishes events when memories are created, updated, or deleted.
Consumers can subscribe to events for:
- Real-time memory sync across agents
- Webhook delivery
- Cache invalidation
- Analytics pipelines
"""

import json
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Event types
MEMORY_CREATED = "memory.created"
MEMORY_UPDATED = "memory.updated"
MEMORY_DELETED = "memory.deleted"
MEMORY_SEARCHED = "memory.searched"
SESSION_CREATED = "session.created"
HANDOFF_EXECUTED = "handoff.executed"


class EventBus:
    """Redis-backed pub/sub event bus.

    Works with any Redis-compatible client (redis-py, valkey).
    Falls back gracefully when Redis is unavailable.
    """

    def __init__(self, redis=None):
        self._redis = redis
        self._local_subscribers: dict[str, list[Callable]] = {}

    @property
    def enabled(self) -> bool:
        return self._redis is not None

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish an event to the bus."""
        payload = json.dumps({"type": event_type, "data": data})

        # Publish to Redis pub/sub if available
        if self._redis:
            try:
                await self._redis.publish(f"mb:{event_type}", payload)
            except Exception:
                logger.warning("Redis publish failed for %s", event_type, exc_info=True)

        # Also notify local subscribers
        for cb in self._local_subscribers.get(event_type, []):
            try:
                await cb(data)
            except Exception:
                logger.warning("Local subscriber failed for %s", event_type, exc_info=True)

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """Register a local subscriber for an event type."""
        if event_type not in self._local_subscribers:
            self._local_subscribers[event_type] = []
        self._local_subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """Remove a local subscriber."""
        subs = self._local_subscribers.get(event_type, [])
        if callback in subs:
            subs.remove(callback)
