from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4
from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    """Types of agent memory — episodic, semantic, procedural, scratchpad."""
    episodic = "episodic"
    """What happened: conversations, events, decisions, failures."""
    semantic = "semantic"
    """What we know: facts, preferences, knowledge, architecture."""
    procedural = "procedural"
    """How we do things: learned workflows, best practices, action chains."""
    scratchpad = "scratchpad"
    """Temporary collaborative workspace that auto-expires after a TTL."""


class MemoryEntry(BaseModel):
    """A single memory entry stored for an agent session."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    agent_id: str
    key: str
    value: Any
    tags: list[str] = Field(default_factory=list)
    memory_type: MemoryType = MemoryType.episodic
    """Type of memory: episodic (what happened), semantic (what we know), procedural (how we do things)."""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ttl_seconds: Optional[int] = None
    """Seconds after which this memory expires. None = never expires."""
    superseded_by: Optional[str] = None
    """If set, this memory has been superseded by another memory with the same key+project. Points to the newer memory's ID."""
    project: Optional[str] = None
    """Project scope for multi-tenant isolation."""
    conflicts_resolved: int = 0
    """Number of conflicting memories that were superseded during this write (transient, not persisted)."""


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
    stripe_customer_id: str = ""
    """Stripe customer ID."""
    tier: str = "free"
    """Tier name: free, starter, pro, enterprise."""
    pending_tier: str = ""
    """If set, the tier to switch to when the current billing period ends."""
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
    memory_type: MemoryType = MemoryType.episodic
    """Type of memory: episodic (what happened), semantic (what we know), procedural (how we do things)."""
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


class PermissionScope(str, Enum):
    """Role-based scope levels for agent permissions.
    
    Hierarchy: read < write < admin
    - read: can read memories
    - write: can read and write memories
    - admin: can read, write, delete, and manage permissions
    """
    read = "read"
    write = "write"
    admin = "admin"


# ── Scope utility functions ───────────────────────────────────────────────────


def scope_to_level(scope: Optional[str]) -> int:
    """Convert a scope string to a numeric level for hierarchy comparisons."""
    if scope is None:
        return 0
    mapping = {"read": 1, "write": 2, "admin": 3}
    return mapping.get(scope, 0)


def scope_implies(scope: Optional[str], required: str) -> bool:
    """Check if a scope level implies (is >=) the required scope level.
    
    Returns True if scope is None (backward compat — no scope = full access).
    """
    if scope is None:
        return True
    return scope_to_level(scope) >= scope_to_level(required)


def derive_scope_bools(perm: "AgentPermission") -> tuple[bool, bool, bool]:
    """Derive can_read/can_write/can_delete booleans from scope if set.
    
    If scope is set, it takes precedence over the individual boolean fields.
    Returns (can_read, can_write, can_delete).
    """
    if perm.scope is None:
        return perm.can_read, perm.can_write, perm.can_delete
    can_read = perm.scope in (PermissionScope.read, PermissionScope.write, PermissionScope.admin)
    can_write = perm.scope in (PermissionScope.write, PermissionScope.admin)
    can_delete = perm.scope == PermissionScope.admin
    return can_read, can_write, can_delete


class AgentPermission(BaseModel):
    """Permission rule for a specific agent.
    
    Supports two modes:
    1. Modern (recommended): Set ``scope`` to one of "read", "write", "admin".
       Boolean flags are derived from the scope hierarchy.
    2. Legacy (backward compat): Set ``can_read``, ``can_write``, ``can_delete``
       individually. If ``scope`` is None, the booleans are used directly.
    
    ``allowed_agent_types`` is an optional whitelist. If empty or None,
    all agent types are permitted. If set, only the listed agent types
    may operate under this permission rule.
    """
    agent_id: str
    project: Optional[str] = None
    scope: Optional[PermissionScope] = None
    """Role-based scope. If set, takes precedence over individual booleans."""
    allowed_agent_types: Optional[list[str]] = None
    """Optional agent_type whitelist. None/empty = all types allowed."""
    can_read: bool = True    # Can read memories stored by other agents in this project
    can_write: bool = True   # Can store new memories
    can_delete: bool = False # Can delete memories
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def model_post_init(self, __context) -> None:
        """Derive boolean flags from scope if scope is set."""
        if self.scope is not None:
            self.can_read, self.can_write, self.can_delete = derive_scope_bools(self)


class AgentPermissionUpdate(BaseModel):
    """Request to update agent permissions."""
    agent_id: Optional[str] = None
    scope: Optional[PermissionScope] = None
    """Role-based scope. If set, overrides individual boolean flags."""
    allowed_agent_types: Optional[list[str]] = None
    """Optional agent_type whitelist. None/empty = all types allowed."""
    can_read: Optional[bool] = None
    can_write: Optional[bool] = None
    can_delete: Optional[bool] = None


class ScoreMemoriesResponse(BaseModel):
    """Response body for POST /memories/score."""
    results: list[ScoredMemoryResult]
    count: int

class InboxMessage(BaseModel):
    """A message left by one agent for another (async agent-to-agent communication)."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    from_agent_id: str
    to_agent_id: str
    subject: str = ""
    body: str
    priority: str = "normal"
    """Priority level: low, normal, high, critical."""
    read: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    read_at: Optional[datetime] = None
    project: Optional[str] = None
    """Project scope for multi-tenant isolation."""


class InboxSendRequest(BaseModel):
    """Request body to send an inbox message."""
    from_agent_id: str
    to_agent_id: str
    subject: str = ""
    body: str
    priority: str = "normal"
    project: Optional[str] = None


class InboxQuery(BaseModel):
    """Query parameters for listing inbox messages."""
    agent_id: Optional[str] = None
    unread_only: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    project: Optional[str] = None


class Scratchpad(BaseModel):
    """A temporary collaborative workspace for agents to write and collaborate.

    Scratchpads auto-expire after a TTL (default 30 minutes).
    Content is a list of text entries appended by agents.
    """
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    agent_id: str
    project_id: str = ""
    content: list[str] = Field(default_factory=list)
    """List of text entries appended by agents."""
    contributors: list[str] = Field(default_factory=list)
    """List of agent_ids that have contributed."""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    """Timestamp after which the scratchpad is considered expired."""
    ttl_seconds: int = 1800
    """Time-to-live in seconds (default 30 minutes)."""


class ScratchpadCreate(BaseModel):
    """Request body to create a scratchpad."""
    session_id: str
    agent_id: str
    content: str
    ttl_seconds: int = 1800
    project_id: str = ""


class ScratchpadAppend(BaseModel):
    """Request body to append content to a scratchpad."""
    agent_id: str
    content: str

