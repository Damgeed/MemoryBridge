"""Usage metering for tracking per-organization API consumption.

Records operations (writes, reads, searches, handoffs) and
provides aggregated usage data for billing and tier enforcement.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Tier limits (aligned with pricing: Free $0, Starter $29/mo, Pro $99/mo, Enterprise custom)
# See docs/next-gen-plan.md for pricing rationale
TIER_LIMITS = {
    "free": {
        "max_memories": 1_000,
        "max_sessions": 100,
        "queries_per_day": 10_000,
        "storage_bytes": 100 * 1024 * 1024,  # 100 MB
        "max_projects": 3,
        "max_api_keys": 5,
        "retention_days": 7,
        "price_monthly": 0,
    },
    "starter": {
        "max_memories": 100_000,
        "max_sessions": 1_000,
        "queries_per_day": 50_000,
        "storage_bytes": 500 * 1024 * 1024,  # 500 MB
        "max_projects": 10,
        "max_api_keys": 25,
        "retention_days": 90,
        "price_monthly": 29,
    },
    "pro": {
        "max_memories": 1_000_000,
        "max_sessions": 10_000,
        "queries_per_day": 500_000,
        "storage_bytes": 5 * 1024 * 1024 * 1024,  # 5 GB
        "max_projects": 100,
        "max_api_keys": 100,
        "retention_days": 365,
        "price_monthly": 99,
    },
    "enterprise": {
        "max_memories": 10_000_000,
        "max_sessions": 1_000_000,
        "queries_per_day": 10_000_000,
        "storage_bytes": 100 * 1024 * 1024 * 1024,  # 100 GB
        "max_projects": 1_000,
        "max_api_keys": 1_000,
        "retention_days": 365 * 5,
        "price_monthly": -1,  # Custom pricing
    },
}


class MeteringService:
    """Usage metering and tier enforcement.

    Records API operations and checks tier limits before allowing operations.
    Uses the repository's metric methods for storage.
    """

    def __init__(self, repo=None):
        self.repo = repo

    async def record_operation(
        self,
        project: Optional[str] = None,
        operation: str = "memory_write",
        size: int = 0,
    ) -> None:
        """Record a metered operation.

        Args:
            project: Project ID for scoping
            operation: Type of operation (memory_write, memory_read, search, handoff)
            size: Size of data in bytes (for storage tracking)
        """
        if not self.repo:
            return

        prefix = f"meter:{project or 'default'}" if project else "meter:default"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            await self.repo.increment_metric(f"{prefix}:{operation}:{today}")
            if size:
                # Track storage used (running total, can be reset monthly)
                await self.repo.increment_metric(f"{prefix}:storage_bytes", size)
            # Track total (non-timestamped) for cross-period stats
            await self.repo.increment_metric(f"{prefix}:{operation}:total")
        except Exception:
            logger.warning("Failed to record metering for %s", operation, exc_info=True)

    async def get_usage(
        self,
        project: Optional[str] = None,
        operation: Optional[str] = None,
    ) -> dict:
        """Get current usage for a project.

        Returns a dict with usage counts per operation.
        """
        if not self.repo:
            return {}

        prefix = f"meter:{project or 'default'}" if project else "meter:default"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        usage = {}
        for op in ["memory_write", "memory_read", "search", "handoff"]:
            count = await self.repo.get_metric(f"{prefix}:{op}:{today}") or 0
            usage[op] = count

        storage = await self.repo.get_metric(f"{prefix}:storage_bytes") or 0
        usage["storage_bytes"] = storage

        return usage

    def check_tier_limit(
        self,
        tier: str = "free",
        metric: str = "queries_per_day",
        current_usage: int = 0,
    ) -> tuple[bool, str]:
        """Check if a tier limit has been reached.

        Args:
            tier: Tier name (free, starter, pro, enterprise)
            metric: Metric to check (queries_per_day, max_memories, etc.)
            current_usage: Current usage count

        Returns:
            (is_allowed: bool, message: str)
        """
        limits = TIER_LIMITS.get(tier, TIER_LIMITS["free"])
        limit = limits.get(metric, float("inf"))

        if current_usage >= limit:
            return False, f"{metric.replace('_', ' ').title()} limit reached ({current_usage}/{limit}). Upgrade your plan."

        remaining = limit - current_usage
        if remaining < limit * 0.1:  # Less than 10% remaining
            logger.info("Tier limit warning: %s at %d/%d (%.0f%%)", metric, current_usage, limit, current_usage / limit * 100)

        return True, ""
