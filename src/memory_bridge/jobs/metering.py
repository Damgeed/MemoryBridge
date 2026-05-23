"""Usage aggregation jobs for billing.

Aggregates raw metering data into hourly and daily summaries
suitable for Stripe metered billing or dashboard display.
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


async def aggregate_hourly_usage(repo) -> dict:
    """Aggregate the past hour's usage across all projects.

    Reads raw counters and produces per-project, per-operation summaries.
    This would be called by a cron job every hour.

    Returns:
        Dict mapping project_id -> {operation: count} for the past hour
    """
    now = datetime.now(timezone.utc)
    hour_ago = now - timedelta(hours=1)
    hour_key = hour_ago.strftime("%Y-%m-%d-%H")

    logger.info("Aggregating hourly usage for period ending at %s", now.isoformat())

    # In production, this would:
    # 1. Query all metering keys for the past hour
    # 2. Group by project_id
    # 3. Store aggregated results
    # 4. Send to Stripe for metered billing
    # 5. Reset hourly counters

    return {"period": hour_key, "projects": {}, "total_operations": 0}


async def aggregate_daily_usage(repo) -> dict:
    """Aggregate the past day's usage across all projects.

    Produces daily summaries used for:
    - Tier limit enforcement (did we exceed today's quota?)
    - Dashboard display
    - Email reports
    """
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    day_key = yesterday.strftime("%Y-%m-%d")

    logger.info("Aggregating daily usage for %s", day_key)

    # In production, this would:
    # 1. Query all metering keys for the past day
    # 2. Group by project_id
    # 3. Check tier limits and flag overages
    # 4. Store in aggregation table
    # 5. Send email alerts for overages

    return {"period": day_key, "projects": {}, "total_operations": 0}


async def reset_metering_counters(repo) -> int:
    """Reset daily metering counters.
    Called at midnight UTC to clear daily counters.

    Returns:
        Number of counters reset
    """
    logger.info("Resetting metering counters for new day")
    return 0
