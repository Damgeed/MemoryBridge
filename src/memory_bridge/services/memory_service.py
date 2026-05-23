"""Business logic for memory operations.

Sits between controllers and the repository layer.
Handles auth context, tier limits, caching, and metering.
"""

import json
import logging
from typing import Any, Optional

from ..config import get_settings
from ..models import MemoryEntry, MemoryCreate
from ..repository import MemoryRepository
from ..repository.s3_store import S3Store
from .embedding_service import EmbeddingService
from .metering_service import TIER_LIMITS

logger = logging.getLogger(__name__)


class TierLimitExceeded(Exception):
    """Raised when a tier limit has been exceeded."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


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
        s3_store: Optional[S3Store] = None,
    ):
        self.repo = repo
        self.cache = cache
        self.metering = metering
        self.s3_store = s3_store
        self._default_ttl: Optional[int] = None  # Set from settings
        self._embedding_service = EmbeddingService()

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

        # Check tier limits before storing
        if self.metering:
            usage = await self.metering.get_usage(project=resolved_project)
            tier = auth_context.get("tier", "free") if auth_context else "free"

            # Check max memories limit
            memories_count = usage.get("memory_write", 0)
            allowed, msg = self.metering.check_tier_limit(
                tier=tier,
                metric="max_memories",
                current_usage=memories_count,
            )
            if not allowed:
                raise TierLimitExceeded(msg)

            # Check storage limit
            storage_bytes = usage.get("storage_bytes", 0)
            max_storage = TIER_LIMITS.get(tier, TIER_LIMITS["free"]).get(
                "storage_bytes", 100 * 1024 * 1024
            )
            if storage_bytes >= max_storage:
                raise TierLimitExceeded(
                    f"Storage limit reached ({storage_bytes}/{max_storage}). Upgrade your plan."
                )

        # Validate value size before storing
        value_size = len(json.dumps(payload.value))
        max_value_size = get_settings().max_value_size
        if value_size > max_value_size:
            logger.warning("Memory value too large: %d bytes (limit: %d)", value_size, max_value_size)
            raise TierLimitExceeded(
                f"Value exceeds {max_value_size}-byte limit ({value_size} bytes)"
            )

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

        # Offload large values to S3 (or local fallback)
        if self.s3_store and self.s3_store.needs_offloading(payload.value):
            s3_key = await self.s3_store.store(entry.id, payload.value)
            if s3_key:
                # Replace the full value with a reference pointer
                entry.value = {
                    "__s3_ref__": True,
                    "key": s3_key,
                    "original_type": type(payload.value).__name__,
                }
                logger.info(
                    "Offloaded %d-byte value to S3 (key=%s)",
                    len(json.dumps(payload.value)), s3_key,
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

        # Resolve S3 reference if value was offloaded
        if entry and self.s3_store and self._is_s3_ref(entry.value):
            resolved = await self.s3_store.retrieve(
                memory_id, entry.value["key"]
            )
            if resolved is not None:
                entry.value = resolved
            else:
                logger.warning(
                    "S3 value missing for memory %s (key=%s); keeping reference",
                    memory_id, entry.value.get("key"),
                )

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

    async def search_memories_semantic(
        self,
        query: str,
        project: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryEntry]:
        """Semantic search across memories.

        Generates an embedding vector from the query text using the
        configured EmbeddingService, then delegates to the repository's
        vector search (pgvector for PostgreSQL, brute-force cosine for SQLite).

        Falls back to regular FTS search if embedding generation is
        unavailable (no API key configured).
        """
        query_vector = await self._embedding_service.embed(query)

        if query_vector is None or len(query_vector) == 0:
            # Embedding unavailable — fall back to FTS
            logger.info("Embedding unavailable, falling back to FTS search")
            return await self.repo.search_memories(
                query=query,
                limit=limit,
                offset=offset,
                project=project,
            )

        try:
            return await self.repo.search_memories_semantic(
                query_vector=query_vector,
                project=project,
                limit=limit,
                offset=offset,
            )
        except NotImplementedError:
            # Backend doesn't support vector search — fall back to FTS
            logger.info("Vector search not supported by backend, falling back to FTS")
            return await self.repo.search_memories(
                query=query,
                limit=limit,
                offset=offset,
                project=project,
            )

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory, evicting from cache and cleaning up S3 if needed."""
        # Fetch the entry first to check for S3 references
        entry = await self.repo.get_memory(memory_id)

        result = await self.repo.delete_memory(memory_id)
        if result and self.cache:
            await self.cache.delete_memory(memory_id)

        # Clean up S3 / local storage if value was offloaded
        if result and self.s3_store and entry and self._is_s3_ref(entry.value):
            await self.s3_store.delete(memory_id, entry.value["key"])

        return result

    @staticmethod
    def _is_s3_ref(value: Any) -> bool:
        """Check if a value is an S3 reference pointer."""
        return isinstance(value, dict) and value.get("__s3_ref__") is True
