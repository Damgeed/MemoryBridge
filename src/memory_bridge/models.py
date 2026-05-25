from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4
from pydantic import BaseModel, Field


class MemoryEntry(BaseModel):
    """A single memory entry stored for an agent session."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    agent_id: str
    key: str
    value: Any
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ttl_seconds: Optional[int] = None
    """Seconds after which this memory expires. None = never expires."""
    project: Optional[str] = None
    """Project scope for multi-tenant isolation."""


class Session(BaseModel):
    """Represents an agent's working session."""
    session_id: str
    agent_id: str
    parent_session_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)
    project: Optional[str] = None
    """Project scope for multi-tenant isolation."""


class Subscription(BaseModel):
    """Stripe subscription linked to an organization."""
    id: str = ""
    """Stripe subscription ID."""
    organization_id: str
    """The org that holds this subscription."""
    stripe_customer_id: str = ""
    """Stripe customer ID."""
    tier: str = "free"
    """Tier name: free, starter, pro, enterprise."""
    status: str = "active"
    """Subscription status: active, past_due, canceled, incomplete."""
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HandoffPayload(BaseModel):
    """Payload for agent-to-agent context handoff."""
    from_agent_id: str
    to_agent_id: str
    session_id: str
    context: dict[str, Any]
    handoff_type: str = "full"  # "full", "summary", "selective"
    include_tags: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class User(BaseModel):
    """A registered user with email + password auth.

    Each user is linked to an organization that holds API keys.
    Auth0 users have auth0_sub set; password_hash is empty.
    """
    id: str = Field(default_factory=lambda: str(uuid4()))
    email: str
    password_hash: str = ""
    name: str = ""
    organization_id: str
    auth0_sub: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemoryCreate(BaseModel):
    """Request body to create a memory entry."""
    session_id: str
    agent_id: str
    key: str
    value: Any
    tags: list[str] = Field(default_factory=list)
    ttl_seconds: Optional[int] = None
    """Seconds after which this memory expires. None = never expires."""
    propagate_to_parent: bool = False
    """If True and the session has a parent_session_id, also store a reference copy under the parent session."""
    project: Optional[str] = None
    """Project scope for multi-tenant isolation."""



class MemoryQuery(BaseModel):
    """Parameters for querying memories."""
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    keys: list[str] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
    project: Optional[str] = None
    """Project scope for multi-tenant isolation."""


class MemorySearchResult(BaseModel):
    """Search result wrapper."""
    entries: list[MemoryEntry]
    total: int
