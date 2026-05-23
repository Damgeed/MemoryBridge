"""Consistent hash ring for deterministic project-to-shard mapping."""

import hashlib
import bisect
from typing import Any


class HashRing:
    """Consistent hash ring for shard routing.

    Maps keys to nodes (shards) consistently.
    Adding or removing nodes only affects 1/N of keys.
    """

    def __init__(self, nodes: list[str], replicas: int = 128):
        """
        Args:
            nodes: List of node identifiers
            replicas: Number of virtual nodes per real node
        """
        self.replicas = replicas
        self.ring: dict[int, str] = {}
        self.sorted_keys: list[int] = []

        for node in nodes:
            self.add_node(node)

    def _hash(self, key: str) -> int:
        return int(hashlib.sha256(key.encode()).hexdigest(), 16)

    def add_node(self, node: str) -> None:
        """Add a node with its virtual replicas."""
        for i in range(self.replicas):
            hash_key = self._hash(f"{node}:{i}")
            self.ring[hash_key] = node
        self.sorted_keys = sorted(self.ring.keys())

    def remove_node(self, node: str) -> None:
        """Remove a node and all its virtual replicas."""
        for i in range(self.replicas):
            hash_key = self._hash(f"{node}:{i}")
            self.ring.pop(hash_key, None)
        self.sorted_keys = sorted(self.ring.keys())

    def get_node(self, key: str) -> str:
        """Get the node responsible for a key."""
        if not self.ring:
            raise ValueError("Hash ring is empty")

        hash_key = self._hash(key)
        idx = bisect.bisect(self.sorted_keys, hash_key)
        if idx == len(self.sorted_keys):
            idx = 0
        return self.ring[self.sorted_keys[idx]]

    @property
    def nodes(self) -> list[str]:
        return list(set(self.ring.values()))
