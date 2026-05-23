"""Abstract repository interface for memory storage backends."""

from abc import ABC, abstractmethod
from typing import Optional

from ..models import MemoryEntry, Session


class MemoryRepository(ABC):
    """Interface for memory storage backends."""

    @abstractmethod
    async def initialize(self):
        """Initialize the storage backend (create tables, run migrations)."""
        ...

    @abstractmethod
    async def store_memory(self, entry: MemoryEntry) -> MemoryEntry:
        """Store a memory entry. Replace if same ID exists."""
        ...

    @abstractmethod
    async def get_memory(self, memory_id: str) -> Optional[MemoryEntry]:
        """Retrieve a memory entry by ID. Returns None if not found or expired."""
        ...

    @abstractmethod
    async def query_memories(
        self,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        keys: Optional[list[str]] = None,
        limit: int = 50,
        offset: int = 0,
        project: Optional[str] = None,
    ) -> list[MemoryEntry]:
        """Query memories with optional filters."""
        ...

    @abstractmethod
    async def search_memories(
        self,
        query: str,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        project: Optional[str] = None,
    ) -> list[MemoryEntry]:
        """Full-text search across memory content."""
        ...

    async def search_memories_semantic(
        self,
        query_vector: list[float],
        project: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryEntry]:
        """Search memories by semantic similarity.

        Default implementation falls back to FTS search.
        Override in backends that support vector search (e.g. pgvector).
        """
        raise NotImplementedError("Semantic search not supported by this backend")

    @abstractmethod
    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory by ID. Returns True if deleted."""
        ...

    @abstractmethod
    async def count_memories(self) -> int:
        """Return total number of memories."""
        ...

    @abstractmethod
    async def store_session(self, session: Session) -> Session:
        """Store a session."""
        ...

    @abstractmethod
    async def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        ...

    @abstractmethod
    async def get_session_lineage(self, session_id: str) -> list[str]:
        """Get all ancestor session IDs (parent, grandparent, etc.)."""
        ...

    @abstractmethod
    async def count_sessions(self) -> int:
        """Return total number of sessions."""
        ...

    @abstractmethod
    async def cleanup_expired(self) -> int:
        """Delete expired memories. Returns count deleted."""
        ...

    @abstractmethod
    async def create_api_key(self, label: str, project_id: Optional[str] = None) -> dict:
        """Create a new API key. Returns dict with id, key (plaintext), label, project_id, is_active, created_at."""
        ...

    @abstractmethod
    async def authenticate_key(self, plain_key: str) -> Optional[dict]:
        """Validate a plaintext API key. Returns key info dict or None."""
        ...

    @abstractmethod
    async def list_api_keys(self) -> list[dict]:
        """List all API keys."""
        ...

    @abstractmethod
    async def revoke_api_key(self, key_id: str) -> bool:
        """Revoke an API key by ID. Returns True if revoked."""
        ...

    @abstractmethod
    async def record_metric(self, key: str, value) -> None:
        ...

    @abstractmethod
    async def get_metric(self, key: str):
        ...

    @abstractmethod
    async def get_all_metrics(self) -> dict:
        ...

    @abstractmethod
    async def increment_metric(self, key: str, delta: int = 1) -> int:
        ...

    @abstractmethod
    async def initialize_metric(self, key: str, default_value) -> None:
        ...
