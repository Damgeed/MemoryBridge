"""Procedural memory — learn and store agent workflow patterns.

Tracks action chains (sequences of actions agents perform) and
detects repeating patterns. When a pattern is detected N times,
it's stored as a procedural memory that agents can query and reuse.

This is v1 — simple frequency-based pattern detection.
"""

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from ..models import MemoryEntry, MemoryType
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)

# Min number of times an action chain must repeat to be saved as a procedure
_MIN_REPETITIONS = 3

# Max steps in a recorded action chain
_MAX_CHAIN_LENGTH = 20


class ProceduralMemoryService:
    """Records agent action chains and detects reusable workflow patterns."""

    def __init__(self, repo: MemoryRepository):
        self._repo = repo

    async def record_action(
        self,
        agent_id: str,
        session_id: str,
        action: str,
        context: Optional[dict[str, Any]] = None,
        project: Optional[str] = None,
    ) -> None:
        """Record a single action in the agent's current session chain.

        Each session maintains a running action chain. When the chain
        reaches a natural breakpoint, check for repeating patterns.
        """
        chain_key = f"proc:chain:{session_id}"
        memory_value = {
            "action": action,
            "context": context or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": agent_id,
            "session_id": session_id,
        }

        # Append this action to the session's action chain
        existing = await self._find_chain(session_id, project)
        if existing:
            chain = json.loads(existing.value) if isinstance(existing.value, str) else existing.value
            if not isinstance(chain, list):
                chain = []
        else:
            chain = []

        chain.append(memory_value)

        # Keep chain bounded
        if len(chain) > _MAX_CHAIN_LENGTH:
            chain = chain[-_MAX_CHAIN_LENGTH:]

        # Store the updated chain
        await self._repo.store_memory(MemoryEntry(
            session_id=session_id,
            agent_id=agent_id,
            key=chain_key,
            value=chain,
            tags=["procedural", "chain"],
            memory_type=MemoryType.procedural,
            project=project,
        ))

    async def finalize_session_chain(
        self,
        session_id: str,
        agent_id: str,
        project: Optional[str] = None,
    ) -> Optional[dict]:
        """Finalize a session's action chain and check for patterns.

        Called when a session ends (e.g., handoff, session close).
        Returns the detected pattern info if a new procedure was saved.
        """
        chain_entry = await self._find_chain(session_id, project)
        if not chain_entry:
            return None

        chain = json.loads(chain_entry.value) if isinstance(chain_entry.value, str) else chain_entry.value
        if not isinstance(chain, list) or len(chain) < 2:
            return None

        # Extract action names (without timestamps)
        action_sequence = tuple(
            step["action"] for step in chain if isinstance(step, dict) and "action" in step
        )

        if len(action_sequence) < 2:
            return None

        # Check if this sequence has been seen before
        pattern = await self._detect_pattern(action_sequence, project)
        if pattern:
            return pattern

        return None

    async def _find_chain(
        self, session_id: str, project: Optional[str] = None
    ) -> Optional[MemoryEntry]:
        """Find the action chain memory for a session."""
        memories = await self._repo.query_memories(
            keys=[f"proc:chain:{session_id}"],
            project=project,
            limit=1,
        )
        return memories[0] if memories else None

    async def _detect_pattern(
        self,
        action_sequence: tuple[str, ...],
        project: Optional[str] = None,
    ) -> Optional[dict]:
        """Check if an action sequence forms a repeated pattern.

        v1: Checks if this exact sequence has been seen before
        by counting how many times it appears across all procedural chains.
        """
        # Query existing procedural memories with pattern tags
        existing_patterns = await self._repo.query_memories(
            tags=["procedural", "pattern"],
            project=project,
            limit=100,
        )

        sequence_json = json.dumps(list(action_sequence))
        pattern_key = f"proc:pattern:{hash(sequence_json)}"

        # Check if this exact pattern already exists
        for mem in existing_patterns:
            if mem.key == pattern_key:
                # Pattern already registered — increment count
                val = json.loads(mem.value) if isinstance(mem.value, str) else mem.value
                count = val.get("count", 1) + 1
                val["count"] = count
                val["last_seen"] = datetime.now(timezone.utc).isoformat()
                mem.value = val
                await self._repo.store_memory(mem)
                logger.info("Procedural pattern repeated %d times: %s", count, " → ".join(action_sequence))
                return {"pattern": list(action_sequence), "count": count, "new": False}

        # New pattern — save it with count=1 (will be promoted on repeat)
        pattern_memory = MemoryEntry(
            agent_id="system",
            session_id="system",
            key=pattern_key,
            value={
                "sequence": list(action_sequence),
                "count": 1,
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "last_seen": datetime.now(timezone.utc).isoformat(),
            },
            tags=["procedural", "pattern"],
            memory_type=MemoryType.procedural,
            project=project,
        )
        await self._repo.store_memory(pattern_memory)
        return {"pattern": list(action_sequence), "count": 1, "new": True}
