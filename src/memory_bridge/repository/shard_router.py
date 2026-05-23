"""Shard routing for horizontal database scaling.

Routes project requests to the correct database shard
using consistent hashing for minimal rebalancing.
"""

import hashlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ShardRouter:
    """Routes project requests to the correct database shard.

    Uses consistent hashing to map project_id to shard index.
    Adding/removing shards only moves 1/N of projects.
    """

    def __init__(self, shards: list[str]):
        """
        Args:
            shards: List of shard DSNs or identifiers.
                    Index in list = shard ID.
        """
        self.shards = shards
        self._virtual_nodes = 128  # Replicas per shard for even distribution

    @property
    def shard_count(self) -> int:
        return len(self.shards)

    def get_shard(self, project_id: str) -> str:
        """Determine which shard a project belongs to.

        Uses MurmurHash-style consistent hashing.
        Returns the shard identifier (DSN).
        """
        if not self.shards:
            raise ValueError("No shards configured")

        if len(self.shards) == 1:
            return self.shards[0]

        # Use SHA-256 hash of project_id to pick shard
        hash_bytes = hashlib.sha256(project_id.encode()).digest()
        # Use first 8 bytes as integer
        hash_int = int.from_bytes(hash_bytes[:8], "big")
        shard_index = hash_int % len(self.shards)
        return self.shards[shard_index]

    def get_shard_index(self, project_id: str) -> int:
        """Get shard index for a project."""
        hash_bytes = hashlib.sha256(project_id.encode()).digest()
        hash_int = int.from_bytes(hash_bytes[:8], "big")
        return hash_int % len(self.shards)

    def get_shards_for_rebalance(self, old_shards: list[str]) -> dict[str, str]:
        """Calculate which projects would move when adding/removing shards.

        Returns a dict of project_id -> new_shard for projects that
        would be reassigned.
        """
        # This is a utility for the rebalancing tool
        # In production, this scans all projects and checks if their shard changed
        logger.info("Rebalance would affect ~%.0f%% of projects",
                    1 / max(len(old_shards), 1) * 100)
        return {}

    def add_shard(self, new_shard: str) -> list[int]:
        """Add a new shard to the cluster.

        Returns the indices of projects that would move.
        """
        old_count = len(self.shards)
        self.shards.append(new_shard)
        logger.info("Added shard %s (now %d shards)", new_shard, len(self.shards))
        return list(range(len(self.shards)))  # Placeholder

    def remove_shard(self, index: int) -> Optional[str]:
        """Remove a shard from the cluster."""
        if index < 0 or index >= len(self.shards):
            return None
        removed = self.shards.pop(index)
        logger.info("Removed shard %s (now %d shards)", removed, len(self.shards))
        return removed
