"""Business logic for memory operations.

Sits between controllers and the repository layer.
Handles auth context, tier limits, caching, and metering.
"""

import logging
from typing import Optional

from ..models import MemoryEntry, MemoryCreate
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)


class MemoryService:
    """Service layer for memory CRUD operations.

    Responsibilities:
    - Resolve project scope from auth context
    - Apply default TTL if not specified
    - Enforce tier limits (future: max memories, storage)
    - Write-through cache for hot memories
    - Meter usage for billing
    """

    def __init__(
        self,
        repo: MemoryRepository,
        cache=None,  # Optional CacheService
        metering=None,  # Optional MeteringService
    ):
        self.repo = repo
        self.cache = cache
        self.metering = metering
        self._default_ttl: Optional[int] = None  # Set from settings

    async def create_memory(
        self,
        payload: MemoryCreate,
        project: Optional[str] = None,
        auth_context: Optional[dict] = None,
    ) -> MemoryEntry:
        """Create a memory entry with business logic.

        - Infers project from auth context if not explicitly set
        - Applies default TTL if payload has none and server default is set
        - Propagates to parent session if requested
        - Caches the entry for fast retrieval
        - Records usage for billing
        """
        # Resolve project scope from auth context if not explicitly provided
        resolved_project = payload.project or project
        if resolved_project is None and auth_context:
            resolved_project = auth_context.get("project_id")

        # Apply default TTL if set at service level
        ttl = payload.ttl_seconds
        if ttl is None and self._default_ttl:
            ttl = self._default_ttl

        # Build the entry
        entry = MemoryEntry(
            session_id=payload.session_id,
            agent_id=payload.agent_id,
            key=payload.key,
            value=payload.value,
            tags=payload.tags,
            ttl_seconds=ttl,
            project=resolved_project,
        )

        # Store
        result = await self.repo.store_memory(
            entry, propagate_to_parent=payload.propagate_to_parent
        )

        # Cache (write-through for hot memories)
        if self.cache:
            await self.cache.set_memory(result)

        # Meter
        if self.metering:
            await self.metering.record_operation(
                project=resolved_project,
                operation="memory_write",
                size=len(str(payload.value)),
            )

        return result

    async def get_memory(
        self, memory_id: str, project: Optional[str] = None
    ) -> Optional[MemoryEntry]:
        """Get a memory by ID, with cache read-through."""
        # Try cache first
        if self.cache:
            cached = await self.cache.get_memory(memory_id)
            if cached:
                return cached

        # Fall through to repo
        entry = await self.repo.get_memory(memory_id)

        # Populate cache on miss
        if entry and self.cache:
            await self.cache.set_memory(entry)

        return entry

    async def query_memories(
        self,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        keys: Optional[list[str]] = None,
        limit: int = 50,
        offset: int = 0,
        project: Optional[str] = None,
        include_lineage: bool = False,
    ) -> list[MemoryEntry]:
        """Query memories with filters and optional lineage traversal."""
        if include_lineage and session_id:
            return await self.repo.query_memories_lineage(
                session_id=session_id,
                agent_id=agent_id,
                tags=tags,
                keys=keys,
                limit=limit,
                offset=offset,
                project=project,
            )
        return await self.repo.query_memories(
            session_id=session_id,
            agent_id=agent_id,
            tags=tags,
            keys=keys,
            limit=limit,
            offset=offset,
            project=project,
        )

    async def search_memories(
        self,
        query: str,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        project: Optional[str] = None,
    ) -> list[MemoryEntry]:
        """Full-text search."""
        return await self.repo.search_memories(
            query=query,
            session_id=session_id,
            agent_id=agent_id,
            limit=limit,
            offset=offset,
            project=project,
        )

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory, evicting from cache."""
        result = await self.repo.delete_memory(memory_id)
        if result and self.cache:
            await self.cache.delete_memory(memory_id)
        return result
