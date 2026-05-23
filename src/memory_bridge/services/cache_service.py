"""Redis-backed read-through cache for hot memories."""

import json
import logging
from typing import Optional

from ..models import MemoryEntry

logger = logging.getLogger(__name__)


class CacheService:
    """Redis-backed cache for memory entries.

    Provides read-through caching with TTL.
    Falls back gracefully if Redis is unavailable.
    """

    def __init__(self, redis_client=None, default_ttl: int = 300):
        """Initialize CacheService.

        Args:
            redis_client: Redis/ValKey async client (optional — cache disabled if None)
            default_ttl: Default TTL in seconds for cached entries
        """
        self._redis = redis_client
        self._default_ttl = default_ttl

    @property
    def enabled(self) -> bool:
        """Whether the cache is active."""
        return self._redis is not None

    async def get_memory(self, memory_id: str) -> Optional[MemoryEntry]:
        """Get a memory from cache. Returns None if not cached or Redis unavailable."""
        if not self._redis:
            return None
        try:
            data = await self._redis.get(f"mem:{memory_id}")
            if data:
                return MemoryEntry.model_validate_json(data)
        except Exception:
            logger.warning("Cache read failed for %s", memory_id, exc_info=True)
        return None

    async def set_memory(self, entry: MemoryEntry, ttl: Optional[int] = None) -> None:
        """Store a memory in cache."""
        if not self._redis:
            return
        try:
            await self._redis.setex(
                f"mem:{entry.id}",
                ttl or self._default_ttl,
                entry.model_dump_json(),
            )
        except Exception:
            logger.warning("Cache write failed for %s", entry.id, exc_info=True)

    async def delete_memory(self, memory_id: str) -> None:
        """Evict a memory from cache."""
        if not self._redis:
            return
        try:
            await self._redis.delete(f"mem:{memory_id}")
        except Exception:
            pass

    async def clear(self) -> None:
        """Clear all cached memories."""
        if not self._redis:
            return
        try:
            await self._redis.flushdb()
        except Exception:
            pass
