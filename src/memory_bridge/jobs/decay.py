"""Memory decay job — auto-prune low-importance, old memories.

Scans memories older than a configurable threshold, scores their
importance, and applies decay actions:
  - Score < 0.3 AND age > threshold  → tag as "decayed" (hidden from normal queries)
  - Score < 0.2 AND age > 2x threshold → delete permanently

This is Memory Bridge's differentiator vs dumb vector databases:
human memory fades — so should agent memory.
"""

import asyncio
import logging
from datetime import datetime, timezone

from ..models import MemoryEntry, MemoryType
from ..services.scoring_service import MemoryScoringService
from ..storage import MemoryStorage

logger = logging.getLogger(__name__)

# ── Config (overridable via env) ──────────────────────────────────────────────

# How often to run the decay pass (seconds). Default: 1 hour.
_DECAY_INTERVAL = int(__import__("os").environ.get(
    "MEMORY_BRIDGE_DECAY_INTERVAL", "3600"
))

# Memories older than this (seconds) are eligible for decay scoring. Default: 7 days.
_DECAY_AGE_THRESHOLD = int(__import__("os").environ.get(
    "MEMORY_BRIDGE_DECAY_AGE", str(7 * 86400)
))

# Importance score below this → apply decay tagging. Default: 0.3.
_DECAY_SCORE_THRESHOLD = float(__import__("os").environ.get(
    "MEMORY_BRIDGE_DECAY_SCORE", "0.3"
))

# Importance score below this AND age > 2x threshold → delete. Default: 0.2.
_DECAY_DELETE_THRESHOLD = float(__import__("os").environ.get(
    "MEMORY_BRIDGE_DECAY_DELETE", "0.2"
))

# Maximum memories to process per pass (safety cap).
_DECAY_BATCH_SIZE = int(__import__("os").environ.get(
    "MEMORY_BRIDGE_DECAY_BATCH", "500"
))


async def _decay_loop(storage: MemoryStorage) -> None:
    """Background loop: periodically scan and decay low-importance memories."""
    scoring = MemoryScoringService()
    logger.info(
        "Decay job started (interval=%ds, age_threshold=%ds, "
        "score_threshold=%.2f, delete_threshold=%.2f, batch=%d)",
        _DECAY_INTERVAL, _DECAY_AGE_THRESHOLD,
        _DECAY_SCORE_THRESHOLD, _DECAY_DELETE_THRESHOLD,
        _DECAY_BATCH_SIZE,
    )

    while True:
        try:
            await asyncio.sleep(_DECAY_INTERVAL)
            await _run_decay_pass(storage, scoring)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Decay job error")


async def _run_decay_pass(
    storage: MemoryStorage,
    scoring: MemoryScoringService,
) -> None:
    """Run a single decay pass: score old memories and apply decay actions."""
    now = datetime.now(timezone.utc)
    cutoff = datetime.fromtimestamp(
        now.timestamp() - _DECAY_AGE_THRESHOLD, tz=timezone.utc
    )
    double_cutoff = datetime.fromtimestamp(
        now.timestamp() - 2 * _DECAY_AGE_THRESHOLD, tz=timezone.utc
    )

    # Fetch all memories older than the threshold (up to batch limit)
    # We query without filters to cover all projects/agents
    old_memories = await _fetch_old_memories(storage, cutoff)

    if not old_memories:
        return

    logger.debug("Decay pass scanning %d memories older than %s", len(old_memories), cutoff.isoformat())

    tagged_count = 0
    deleted_count = 0

    for memory in old_memories:
        # Skip already-archived memories — they'll be cleaned up by TTL
        if "decayed" in memory.tags:
            continue

        # Compute intrinsic importance score (no query context needed)
        importance = scoring._compute_importance(memory)

        if importance < _DECAY_DELETE_THRESHOLD and memory.created_at < double_cutoff:
            # Very low importance AND very old → delete permanently
            try:
                await storage.delete_memory(memory.id)
                deleted_count += 1
            except Exception as e:
                logger.warning("Failed to delete decayed memory %s: %s", memory.id, e)
            continue

        if importance < _DECAY_SCORE_THRESHOLD:
            # Low importance but not old enough to delete → tag as decayed
            try:
                memory.tags = list(set(memory.tags + ["decayed"]))
                await storage.store_memory(memory)
                tagged_count += 1
            except Exception as e:
                logger.warning("Failed to tag decayed memory %s: %s", memory.id, e)

    if tagged_count or deleted_count:
        logger.info(
            "Decay pass complete: tagged %d, deleted %d (scanned %d)",
            tagged_count, deleted_count, len(old_memories),
        )


async def _fetch_old_memories(
    storage: MemoryStorage,
    cutoff: datetime,
    limit: int = _DECAY_BATCH_SIZE,
) -> list[MemoryEntry]:
    """Fetch memories older than cutoff, across all projects."""
    # Use query_memories with no filters to get all, then filter by age client-side
    all_memories = await storage.query_memories(limit=limit)
    return [m for m in all_memories if m.created_at < cutoff]
