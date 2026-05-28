"""Abstract repository interface for memory storage backends."""

from abc import ABC, abstractmethod
from typing import Any, Optional

from ..models import MemoryEntry, Session, Subscription, AgentPermission


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

    # ── Embedding Storage ────────────────────────────────────────────────────

    async def store_embedding(
        self, memory_id: str, embedding: list[float]
    ) -> None:
        """Store an embedding vector for a memory entry."""
        raise NotImplementedError("Embedding storage not supported by this backend")

    async def get_embedding(self, memory_id: str) -> Optional[list[float]]:
        """Retrieve the stored embedding for a memory entry.

        Returns None if no embedding has been stored.
        """
        raise NotImplementedError("Embedding retrieval not supported by this backend")

    async def search_by_vector(
        self, embedding: list[float], limit: int = 10
    ) -> list[str]:
        """Search memory IDs by vector similarity.

        Returns memory IDs ordered by cosine similarity descending
        (most similar first). Only returns IDs with stored embeddings.

        Default implementation raises NotImplementedError.
        """
        raise NotImplementedError("Vector search not supported by this backend")

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

    # ── Subscription Management ──────────────────────────────────────────────

    @abstractmethod
    async def store_subscription(self, sub: Subscription) -> Subscription:
        """Store a subscription record. Replace if same id exists."""
        ...

    @abstractmethod
    async def get_subscription_by_org(self, organization_id: str) -> Optional[Subscription]:
        """Get subscription by organization ID. Returns None if not found."""
        ...

    @abstractmethod
    async def get_subscription_by_stripe_customer(self, customer_id: str) -> Optional[Subscription]:
        """Get subscription by Stripe customer ID. Returns None if not found."""
        ...

    @abstractmethod
    async def get_subscription_by_id(self, sub_id: str) -> Optional[Subscription]:
        """Get subscription by its Stripe subscription ID. Returns None if not found."""
        ...

    @abstractmethod
    async def update_subscription_tier(self, sub_id: str, tier: str) -> Optional[Subscription]:
        """Update the tier of a subscription by Stripe subscription ID.
        Returns the updated Subscription or None if not found."""
        ...

    # ── User Management ─────────────────────────────────────────────────

    @abstractmethod
    async def create_user(self, user) -> dict:
        """Create a user record. Returns user dict."""
        ...

    @abstractmethod
    async def get_user_by_email(self, email: str):
        """Look up user by email. Returns user dict or None."""
        ...

    @abstractmethod
    async def get_user_by_oauth(self, provider: str, provider_user_id: str):
        """Look up user by OAuth provider + user ID. Returns user dict or None."""
        ...

    @abstractmethod
    async def link_oauth_account(self, user_id: str, provider: str, provider_user_id: str):
        """Link an OAuth account to an existing user."""

    @abstractmethod
    async def get_user_by_organization_id(self, org_id: str):
        """Look up user by organization_id. Returns user dict or None."""
        ...

    @abstractmethod
    async def update_user_stripe_customer(self, user_id: str, customer_id: str) -> bool:
        """Update the stripe_customer_id for a user. Returns True if updated."""
        ...

    @abstractmethod
    async def update_user_organization_id(self, user_id: str, organization_id: str) -> bool:
        """Update organization_id for a user. Returns True if updated."""
        ...

    # ── Audit Log ───────────────────────────────────────────────────────────

    @abstractmethod
    async def record_audit_entry(
        self,
        id: str,
        timestamp: str,
        actor_type: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: Optional[str],
        project_id: Optional[str],
        ip_address: Optional[str],
        details: Any,
        previous_hash: Optional[str],
        hash: str,
    ) -> None:
        ...

    @abstractmethod
    async def get_last_audit_hash(self) -> Optional[str]:
        ...

    @abstractmethod
    async def get_all_audit_entries(self) -> list[dict]:
        ...

    # ── Agent ACL (Access Control List) ────────────────────────────────────

    @abstractmethod
    async def set_agent_permission(self, perm: AgentPermission) -> None:
        """Set or update permission for an agent."""
        ...

    @abstractmethod
    async def get_agent_permission(
        self, agent_id: str, project: Optional[str] = None
    ) -> Optional[AgentPermission]:
        """Get permission for an agent. Returns None if no rule set (default = full access)."""
        ...

    @abstractmethod
    async def list_agent_permissions(
        self, project: Optional[str] = None
    ) -> list[AgentPermission]:
        """List all agent permission rules, optionally filtered by project."""
        ...

    @abstractmethod
    async def delete_agent_permission(
        self, agent_id: str, project: Optional[str] = None
    ) -> bool:
        """Delete permission rule for an agent. Returns True if deleted."""
        ...

    # ── Webhook Subscriptions ────────────────────────────────────────────────

    @abstractmethod
    async def store_webhook_subscription(self, sub: dict) -> None:
        """Store or update a webhook subscription."""
        ...

    @abstractmethod
    async def get_webhook_subscription(self, sub_id: str) -> Optional[dict]:
        """Get a webhook subscription by ID. Returns None if not found."""
        ...

    @abstractmethod
    async def list_webhook_subscriptions(
        self, project: Optional[str] = None
    ) -> list[dict]:
        """List webhook subscriptions, optionally filtered by project."""
        ...

    @abstractmethod
    async def remove_webhook_subscription(self, sub_id: str) -> bool:
        """Remove a webhook subscription by ID. Returns True if removed."""
        ...

    @abstractmethod
    async def store_webhook_delivery(self, delivery: dict) -> None:
        """Record a webhook delivery attempt."""
        ...

    @abstractmethod
    async def get_webhook_deliveries(
        self, subscription_id: str, limit: int = 50, offset: int = 0
    ) -> tuple[list[dict], int]:
        """Get paginated delivery history for a webhook subscription.
        Returns (deliveries, total_count)."""
        ...

    @abstractmethod
    async def cleanup_old_webhook_deliveries(self, max_age_days: int = 30) -> int:
        """Delete webhook delivery records older than max_age_days.
        Returns the number of records deleted."""
        ...
