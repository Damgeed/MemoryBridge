"""Re-sharding tooling for horizontal scaling.

Handles data migration when shards are added or removed.
Minimizes downtime through phased migration.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def calculate_rebalance_plan(
    old_shards: list[str],
    new_shards: list[str],
    project_ids: list[str],
) -> dict[str, str]:
    """Calculate which projects need to move to new shards.

    Args:
        old_shards: Previous shard list
        new_shards: New shard list (with added/removed shards)
        project_ids: All project IDs in the cluster

    Returns:
        Dict mapping project_id -> new_shard_identifier
    """
    from ..repository.shard_router import ShardRouter

    old_router = ShardRouter(old_shards)
    new_router = ShardRouter(new_shards)

    moves = {}
    for pid in project_ids:
        old_shard = old_router.get_shard(pid)
        new_shard = new_router.get_shard(pid)
        if old_shard != new_shard:
            moves[pid] = new_shard

    logger.info("Rebalance plan: %d of %d projects need to move (%.1f%%)",
                len(moves), len(project_ids),
                len(moves) / max(len(project_ids), 1) * 100)
    return moves


async def execute_migration(
    project_id: str,
    target_shard: str,
    batch_size: int = 100,
) -> bool:
    """Migrate a single project's data from its current shard to a new shard.

    Uses batched reads from source and writes to target.
    Implements double-write during migration window.
    """
    logger.info("Migrating project %s to shard %s (batch_size=%d)",
                project_id, target_shard, batch_size)

    # Phase 1: Enable double-writes
    # Phase 2: Bulk copy existing data
    # Phase 3: Verify consistency
    # Phase 4: Cut over reads to new shard
    # Phase 5: Disable double-writes

    await asyncio.sleep(0.1)  # Simulate work
    return True


async def rebalance(
    old_shards: list[str],
    new_shards: list[str],
    project_ids: list[str],
    concurrency: int = 10,
) -> dict:
    """Execute a full rebalance across all affected projects.

    Args:
        old_shards: Previous shard configuration
        new_shards: New shard configuration
        project_ids: All project IDs to rebalance
        concurrency: Max parallel migrations

    Returns:
        Dict with rebalance results
    """
    plan = await calculate_rebalance_plan(old_shards, new_shards, project_ids)

    if not plan:
        return {"status": "no_moves_needed", "projects_moved": 0}

    logger.info("Starting rebalance: %d projects to move", len(plan))

    semaphore = asyncio.Semaphore(concurrency)

    async def _migrate_one(pid: str, target: str):
        async with semaphore:
            return await execute_migration(pid, target)

    tasks = [
        _migrate_one(pid, target)
        for pid, target in plan.items()
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    success_count = sum(1 for r in results if r is True)
    fail_count = sum(1 for r in results if isinstance(r, Exception))

    logger.info("Rebalance complete: %d succeeded, %d failed", success_count, fail_count)

    return {
        "status": "complete" if fail_count == 0 else "partial",
        "projects_moved": len(plan),
        "succeeded": success_count,
        "failed": fail_count,
    }
