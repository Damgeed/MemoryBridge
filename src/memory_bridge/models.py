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
    stripe_customer_id: str = ""
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


class ExtractFactsRequest(BaseModel):
    """Request body for POST /memories/extract."""
    text: str = Field(..., min_length=1)
    source_key: Optional[str] = None
    store_facts: bool = False
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    max_facts: int = Field(default=10, ge=1, le=25)


class MemorySearchResult(BaseModel):
    """Search result wrapper."""
    entries: list[MemoryEntry]
    total: int


class ScoreMemoriesRequest(BaseModel):
    """Request body for POST /memories/score."""
    memories: Optional[list[str]] = None
    """Optional list of memory IDs to score. If omitted, scores top memories by filters."""
    query: str = ""
    """Optional natural language query for relevance scoring."""
    limit: int = Field(default=20, ge=1, le=200)
    """Max results to return (when no specific memory IDs given)."""
    session_id: Optional[str] = None
    """Optional session filter."""
    agent_id: Optional[str] = None
    """Optional agent filter."""
    weights: Optional[dict[str, float]] = None
    """Optional custom weights: {"recency": 0.3, "relevance": 0.5, "importance": 0.2}"""


class ScoredMemoryResult(BaseModel):
    """A single scored memory result."""
    memory: MemoryEntry
    score: float
    recency_score: float
    relevance_score: float
    importance_score: float


class ScoreMemoriesResponse(BaseModel):
    """Response body for POST /memories/score."""
    results: list[ScoredMemoryResult]
    count: int
