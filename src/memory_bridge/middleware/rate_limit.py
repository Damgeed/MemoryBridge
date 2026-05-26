"""Rate limit middleware with Redis-backed sliding window and tier awareness."""

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class RedisRateLimiter:
    """Sliding-window rate limiter backed by Redis.

    Uses Redis INCR with TTL for atomic sliding window.
    Falls back to per-process limiting if Redis is unavailable.

    Supports per-API-key rate tracking with tier-aware limits.
    """

    def __init__(self, redis=None, requests_per_minute: int = 60):
        self._redis = redis
        self.rate = requests_per_minute
        # Fallback in-memory limiter (if Redis unavailable)
        self._fallback_buckets: dict[str, list[float]] = {}

        # Tier rate limits (requests per minute)
        # The "free" tier uses the configured requests_per_minute so that
        # the MEMORY_BRIDGE_RATE_LIMIT env var is respected.
        self.tier_limits = {
            "free": self.rate,
            "demo": 5,
            "starter": 600,
            "pro": 1200,
            "enterprise": 6000,
        }

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

    async def check_with_key(
        self,
        key_id: Optional[str] = None,
        tier: str = "free",
        client_ip: str = "unknown",
    ) -> bool:
        """Check rate limit with per-API-key tracking and tier awareness.

        Uses key_id as primary rate limit key (per-key limiting).
        Falls back to IP-based limiting if no key_id provided.

        Args:
            key_id: API key identifier for per-key tracking.
            tier: Tier name (free, starter, pro, enterprise).
            client_ip: Client IP address for fallback key.

        Returns:
            True if the request is under the rate limit.
        """
        rate_key = key_id if key_id else client_ip
        max_rate = self.tier_limits.get(tier, 60)

        if self._redis:
            return await self._check_redis_with_rate(rate_key, max_rate)
        return self._check_memory_with_rate(rate_key, max_rate)

    async def _check_redis(self, key: str) -> bool:
        """Redis-backed sliding window using sorted sets."""
        return await self._check_redis_with_rate(key, self.rate)

    async def _check_redis_with_rate(self, key: str, max_rate: int) -> bool:
        """Redis-backed sliding window with configurable rate."""
        now = time.time()
        window_key = f"ratelimit:{key}:{int(now // 60)}"
        try:
            count = await self._redis.incr(window_key)
            if count == 1:
                await self._redis.expire(window_key, 120)
            return count <= max_rate
        except Exception:
            logger.warning("Redis rate limit check failed, falling back to in-memory")
            return self._check_memory_with_rate(key, max_rate)

    def _check_memory(self, key: str) -> bool:
        """Fallback in-memory sliding window."""
        return self._check_memory_with_rate(key, self.rate)

    def _check_memory_with_rate(self, key: str, max_rate: int) -> bool:
        """Fallback in-memory sliding window with configurable rate."""
        now = time.monotonic()
        window = 60.0
        buckets = self._fallback_buckets
        # Prune
        buckets[key] = [t for t in buckets.get(key, []) if now - t < window]
        if len(buckets[key]) >= max_rate:
            return False
        buckets[key].append(now)
        return True
