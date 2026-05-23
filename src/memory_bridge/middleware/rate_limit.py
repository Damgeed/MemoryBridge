"""Rate limit middleware with Redis-backed sliding window."""

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class RedisRateLimiter:
    """Sliding-window rate limiter backed by Redis.

    Uses Redis INCR with TTL for atomic sliding window.
    Falls back to per-process limiting if Redis is unavailable.
    """

    def __init__(self, redis=None, requests_per_minute: int = 60):
        self._redis = redis
        self.rate = requests_per_minute
        # Fallback in-memory limiter (if Redis unavailable)
        self._fallback_buckets: dict[str, list[float]] = {}

    @property
    def enabled(self) -> bool:
        """Whether Redis-backed limiting is active."""
        return self._redis is not None

    async def check(self, key: str = "default") -> bool:
        """Check if a request from this key is allowed.

        Returns True if under the rate limit.
        """
        if self._redis:
            return await self._check_redis(key)
        return self._check_memory(key)

    async def _check_redis(self, key: str) -> bool:
        """Redis-backed sliding window using sorted sets."""
        now = time.time()
        window = 60.0
        window_key = f"ratelimit:{key}:{int(now // 60)}"
        try:
            count = await self._redis.incr(window_key)
            if count == 1:
                await self._redis.expire(window_key, 120)
            return count <= self.rate
        except Exception:
            logger.warning("Redis rate limit check failed, falling back to in-memory")
            return self._check_memory(key)

    def _check_memory(self, key: str) -> bool:
        """Fallback in-memory sliding window."""
        now = time.monotonic()
        window = 60.0
        buckets = self._fallback_buckets
        # Prune
        buckets[key] = [t for t in buckets.get(key, []) if now - t < window]
        if len(buckets[key]) >= self.rate:
            return False
        buckets[key].append(now)
        return True
