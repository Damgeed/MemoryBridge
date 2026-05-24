"""Simple in-memory token bucket rate limiter."""

import time
from collections import defaultdict


class RateLimiter:
    """Sliding-window rate limiter keyed by client IP."""

    def __init__(self, requests_per_minute: int = 60, demo_requests_per_minute: int = 5):
        self.rate = requests_per_minute
        self.demo_rate = demo_requests_per_minute
        self.buckets: dict[str, list[float]] = defaultdict(list)

    async def check(self, key: str = "default") -> bool:
        """Check if a request from this key is allowed. Returns True if under limit."""
        now = time.monotonic()
        window = 60.0
        # Prune entries older than the window
        self.buckets[key] = [t for t in self.buckets[key] if now - t < window]
        max_rate = self.demo_rate if key.startswith("demo:") else self.rate
        if len(self.buckets[key]) >= max_rate:
            return False
        self.buckets[key].append(now)
        return True
