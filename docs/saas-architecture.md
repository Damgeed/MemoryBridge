# Memory Bridge SaaS Architecture

> **Author:** Henry 🧠 — The Architect  
> **Version:** v0.3 → v1.0 (SaaS transformation)  
> **Codebase analyzed:** v0.2.0 (751-line monolithic storage, 426-line main.py, 8 SQLite migrations)

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Storage Architecture](#2-storage-architecture)
3. [Service Layer](#3-service-layer)
4. [Multi-Tenancy Design](#4-multi-tenancy-design)
5. [User & Billing System](#5-user--billing-system)
6. [Background Job System](#6-background-job-system)
7. [Admin API Design](#7-admin-api-design)
8. [Scaling Strategy](#8-scaling-strategy)
9. [File Manifest](#9-file-manifest)
10. [Priority-Ordered Execution Phases](#10-priority-ordered-execution-phases)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Cloudflare / CDN                            │
│              (DDoS protection, TLS termination, caching)            │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
┌────────────────────────────────┴────────────────────────────────────┐
│                     Load Balancer (ALB / HAProxy)                   │
│                  (SSL offload, path-based routing)                  │
└─────────┬──────────────────┬──────────────────┬─────────────────────┘
          │                  │                  │
     ┌────┴────┐        ┌────┴────┐        ┌────┴────┐
     │ Worker 1 │        │ Worker 2 │        │ Worker N │
     │ (FastAPI)│        │ (FastAPI)│        │ (FastAPI)│
     └────┬────┘        └────┬────┘        └────┬────┘
          │                  │                  │
          ├──────────────────┴──────────────────┤
          │          (asyncpg pool)             │
          └───────────────┬─────────────────────┘
                          │
          ┌───────────────┴───────────────────────┐
          │         PostgreSQL (Primary)          │
          │  ┌─────────────────────────────────┐  │
          │  │  ┌──────────────────────────┐   │  │
          │  │  │   tenant_schema_001      │   │  │
          │  │  │   tenant_schema_002      │   │  │
          │  │  │   ... (per-tenant)       │   │  │
          │  │  └──────────────────────────┘   │  │
          │  │  ┌──────────────────────────┐   │  │
          │  │  │   public (users, orgs,   │   │  │
          │  │  │   billing, api_keys)     │   │  │
          │  │  └──────────────────────────┘   │  │
          │  └─────────────────────────────────┘  │
          └───────────┬───────────────────────────┘
                      │
          ┌───────────┴───────────┐
          │  Read Replica Pool   │
          │  (1-5 replicas)      │
          └───────────────────────┘

          ┌───────────────────────────────────────┐
          │           Redis / Valkey              │
          │  - Session cache (memories)           │
          │  - Rate limit buckets                 │
          │  - Job queue (RQ / ARQ)               │
          │  - Lock manager                       │
          │  - Real-time pub/sub                  │
          └───────────────────────────────────────┘

          ┌───────────────────────────────────────┐
          │     Object Storage (S3/MinIO)         │
          │  - Large memory values (>64KB)        │
          │  - Audit logs                         │
          │  - Usage export CSVs                  │
          └───────────────────────────────────────┘
```

### Component Roles

| Component | Role | Technology |
|-----------|------|-----------|
| **API Gateway / LB** | SSL, routing, rate limit at edge | Nginx / ALB |
| **FastAPI Workers** | REST handlers, auth, validation | Uvicorn + Gunicorn |
| **PostgreSQL** | Primary database with read replicas | PostgreSQL 16 + asyncpg |
| **Redis/Valkey** | Cache, rate limiter, job queue, locks | Valkey 8+ |
| **Object Storage** | Large value storage, exports | S3-compatible (MinIO dev, AWS S3 prod) |
| **Job Workers** | Background async processing | ARQ (Redis-backed async job queue) |
| **Prometheus + Grafana** | Metrics + dashboards | prometheus-client + Grafana Cloud |
| **Stripe** | Subscription billing | Stripe API |
| **Resend / SendGrid** | Transactional email | SMTP + API |

---

## 2. Storage Architecture

### 2.1 Dual-Backend Strategy

**Principle:** All storage access goes through an abstract repository interface. SQLite (aiosqlite) is used for **development, testing, and single-user deploys**. PostgreSQL (asyncpg) is used for **production SaaS**.

```
┌──────────────┐     ┌──────────────────────────────┐
│  Controller  │────▶│      Service Layer           │
│  (main.py)   │     │  (business logic, validation) │
└──────────────┘     └──────────────┬───────────────┘
                                    │
                    ┌───────────────┴────────────────┐
                    │     Abstract Repository        │
                    │  (MemoryRepository protocol)   │
                    └───────┬───────────────┬────────┘
                            │               │
              ┌─────────────┴──┐    ┌───────┴─────────────┐
              │ SQLiteRepo    │    │ PostgreSQLRepo      │
              │ (aiosqlite)   │    │ (asyncpg)           │
              │ dev/testing   │    │ production          │
              └───────────────┘    └─────────────────────┘
```

**Repository protocol** (defined as a Python Protocol / ABC):

```python
class MemoryRepository(ABC):
    """Abstract data access layer."""

    @abstractmethod
    async def initialize(self): ...
    @abstractmethod
    async def store_memory(self, entry: MemoryEntry) -> MemoryEntry: ...
    @abstractmethod
    async def get_memory(self, memory_id: str) -> Optional[MemoryEntry]: ...
    @abstractmethod
    async def query_memories(self, filters: MemoryQuery) -> list[MemoryEntry]: ...
    @abstractmethod
    async def delete_memory(self, memory_id: str) -> bool: ...
    @abstractmethod
    async def search_memories(self, query: str, filters...) -> list[MemoryEntry]: ...
    @abstractmethod
    async def count_memories(self) -> int: ...
    @abstractmethod
    async def store_session(self, session: Session) -> Session: ...
    @abstractmethod
    async def get_session(self, session_id: str) -> Optional[Session]: ...
    @abstractmethod
    async def get_session_lineage(self, session_id: str) -> list[str]: ...
    @abstractmethod
    async def cleanup_expired(self) -> int: ...
    # API key methods
    @abstractmethod
    async def create_api_key(self, ...) -> dict: ...
    @abstractmethod
    async def authenticate_key(self, key: str) -> Optional[dict]: ...
    @abstractmethod
    async def list_api_keys(self) -> list[dict]: ...
    @abstractmethod
    async def revoke_api_key(self, key_id: str) -> bool: ...
```

### 2.2 PostgreSQL Schema (Production)

**Design decision:** Schema-per-tenant for strong isolation (see §4). The `public` schema holds global tables. Each tenant gets `tenant_{id}` schema.

#### Public Schema (`public`)

```sql
-- ============================================================
-- USERS & ORGANIZATIONS
-- ============================================================

CREATE TABLE public.users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT UNIQUE NOT NULL,
    name            TEXT,
    password_hash   TEXT NOT NULL,           -- bcrypt
    profile_picture TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE public.organizations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    slug            TEXT UNIQUE NOT NULL,    -- for vanity URLs
    owner_id        UUID NOT NULL REFERENCES users(id),
    stripe_customer_id TEXT,
    tier            TEXT NOT NULL DEFAULT 'free',
        -- 'free' | 'starter' | 'pro' | 'enterprise'
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE public.organization_members (
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role            TEXT NOT NULL DEFAULT 'member',
        -- 'owner' | 'admin' | 'member' | 'viewer'
    joined_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (organization_id, user_id)
);

-- ============================================================
-- PROJECTS (Tenant unit — each project gets its own schema)
-- ============================================================

CREATE TABLE public.projects (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL,
    tenant_schema   TEXT NOT NULL UNIQUE,    -- 'tenant_' || id::text
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    max_memories    INTEGER,                 -- tier limit
    max_sessions    INTEGER,                 -- tier limit
    memory_ttl_default INTEGER,              -- default TTL in seconds
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, slug)
);

-- ============================================================
-- BILLING & SUBSCRIPTIONS
-- ============================================================

CREATE TABLE public.subscriptions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organizations(id),
    stripe_subscription_id TEXT UNIQUE,
    stripe_price_id     TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'incomplete',
        -- 'incomplete' | 'active' | 'past_due' | 'canceled' | 'unpaid'
    current_period_start TIMESTAMPTZ,
    current_period_end   TIMESTAMPTZ,
    trial_end           TIMESTAMPTZ,
    cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE public.usage_records (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    project_id      UUID REFERENCES projects(id),
    metric          TEXT NOT NULL,
        -- 'memory_writes' | 'memory_reads' | 'handoffs' | 'storage_bytes'
    quantity        INTEGER NOT NULL DEFAULT 0,
    window_start    TIMESTAMPTZ NOT NULL,
    window_end      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_usage_org_window
    ON public.usage_records (organization_id, window_start DESC);

-- ============================================================
-- API KEYS (moved to public — scoped by project_id)
-- ============================================================

CREATE TABLE public.api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    project_id      UUID REFERENCES projects(id),
    key_hash        TEXT NOT NULL UNIQUE,    -- SHA-256 of mb_sk_...
    key_prefix      TEXT NOT NULL,           -- first 8 chars for identification
    label           TEXT NOT NULL,
    permissions     TEXT[] NOT NULL DEFAULT '{read,write}',
        -- 'read', 'write', 'admin'
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at      TIMESTAMPTZ,
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ
);

CREATE INDEX idx_api_keys_org ON public.api_keys (organization_id);
CREATE INDEX idx_api_keys_hash ON public.api_keys (key_hash);
```

#### Per-Tenant Schema (`tenant_{project_id}`)

```sql
-- Each project gets its own PostgreSQL schema for strong isolation.

CREATE SCHEMA IF NOT EXISTS tenant_abc123;

-- Sessions
CREATE TABLE tenant_abc123.sessions (
    session_id          TEXT NOT NULL,
    agent_id            TEXT NOT NULL,
    parent_session_id   TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata            JSONB NOT NULL DEFAULT '{}',
    -- Lineage tracking for fast parent-chain queries
    lineage_depth       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, agent_id)
);

CREATE INDEX idx_sessions_agent ON tenant_abc123.sessions (agent_id);

-- Memories
CREATE TABLE tenant_abc123.memories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    key             TEXT NOT NULL,
    value           JSONB NOT NULL,
    value_external  BOOLEAN NOT NULL DEFAULT FALSE,
        -- TRUE if value is stored in S3 (values > 64KB)
    value_s3_key    TEXT,
    tags            TEXT[] NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ttl_seconds     INTEGER,
    expires_at      TIMESTAMPTZ GENERATED ALWAYS AS
        (CASE WHEN ttl_seconds IS NOT NULL
              THEN created_at + (ttl_seconds || ' seconds')::INTERVAL
              ELSE NULL END) STORED,
    importance      REAL NOT NULL DEFAULT 1.0,
        -- Future: weight for memory importance scoring
    access_count    INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TIMESTAMPTZ,
    FOREIGN KEY (session_id, agent_id)
        REFERENCES tenant_abc123.sessions(session_id, agent_id)
);

-- Critical performance indexes
CREATE INDEX idx_memories_session ON tenant_abc123.memories (session_id);
CREATE INDEX idx_memories_key    ON tenant_abc123.memories (key);
CREATE INDEX idx_memories_tags   ON tenant_abc123.memories USING GIN (tags);
CREATE INDEX idx_memories_expires ON tenant_abc123.memories (expires_at)
    WHERE expires_at IS NOT NULL;
CREATE INDEX idx_memories_created ON tenant_abc123.memories (created_at DESC);

-- Full-text search (PostgreSQL built-in, no FTS5 dependency)
CREATE TEXT SEARCH CONFIGURATION tenant_abc123.english
    (COPY = pg_catalog.english);
CREATE INDEX idx_memories_fts ON tenant_abc123.memories
    USING GIN (to_tsvector('english'::regconfig, key || ' ' || COALESCE(value::text, '')));

-- Handoff records
CREATE TABLE tenant_abc123.handoffs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_agent_id   TEXT NOT NULL,
    to_agent_id     TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    handoff_type    TEXT NOT NULL,  -- 'full' | 'summary' | 'selective'
    context_summary JSONB,
    key_count       INTEGER NOT NULL DEFAULT 0,
    warnings        TEXT[],
    success         BOOLEAN NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_handoffs_session ON tenant_abc123.handoffs (session_id);
```

### 2.3 Connection Pooling

```python
# Production config
POOL_CONFIG = {
    "min_size": 5,      # Keep 5 connections warm per worker
    "max_size": 20,     # Max 20 connections per worker
    "max_queries": 50000,
    "max_inactive_connection_lifetime": 300.0,
    "command_timeout": 30,
}

# With 10 workers: 50-200 connections total
# PostgreSQL default max_connections = 100 → adjust to 300-500
```

**Pool sizing formula:**
- `workers × pool_max_size = total_connections`
- 10 workers × 20 = 200 connections
- PostgreSQL `max_connections` = workers × (pool_max_size + 3) for headroom
- Connection pooling at PgBouncer level for serverless / ephemeral workers (Phase 2+)

### 2.4 Migration System

```python
# Unified migration system that works for both backends:

class Migration:
    version: int
    description: str
    sql_sqlite: str     # SQLite-compatible DDL
    sql_pg: str         # PostgreSQL-compatible DDL (same or different)

MIGRATIONS = {
    1: Migration(
        version=1,
        description="Initial per-tenant sessions + memories tables",
        sql_sqlite="...",
        sql_pg="...",
    ),
    2: Migration(
        version=2,
        description="Add handoffs table",
        sql_sqlite="...",
        sql_pg="...",
    ),
}
```

Directory structure:

```
src/memory_bridge/migrations/
├── __init__.py
├── base.py                    # Migration base class + registry
├── sqlite/                    # SQLite-specific migration files
│   ├── 001_initial.sql
│   ├── 002_add_handoffs.sql
├── postgresql/                # PostgreSQL-specific migration files
│   ├── 001_initial.sql
│   ├── 002_add_handoffs.sql
├── runner.py                  # Migration runner
├── schema_version.sql         # CREATE TABLE IF NOT EXISTS for both
```

---

## 3. Service Layer

### 3.1 Three-Layer Architecture

```python
# ── Controller Layer (HTTP handlers) ──
# File: src/memory_bridge/controllers/memory_controller.py

router = APIRouter(prefix="/memories", tags=["memories"])

@router.post("", response_model=MemoryEntry)
async def create_memory(
    payload: MemoryCreate,
    request: Request,
    service: MemoryService = Depends(get_memory_service),
):
    """Creates a memory with business logic (TTL defaults, project inheritance, limits)."""
    return await service.create_memory(payload, request.state.auth)


# ── Service Layer (business logic) ──
# File: src/memory_bridge/services/memory_service.py

class MemoryService:
    """Business logic layer — orchestration, validation, limits, cross-cutting concerns."""

    def __init__(self, repo: MemoryRepository, cache: CacheService, metering: MeteringService):
        self.repo = repo
        self.cache = cache
        self.metering = metering

    async def create_memory(self, payload: MemoryCreate, auth: AuthContext) -> MemoryEntry:
        # 1. Enforce project isolation from auth
        project_id = auth.project_id or payload.project
        if not project_id:
            raise HTTPException(400, "Project must be specified or inferred from API key")

        # 2. Apply tier limits
        await self._check_memory_limit(project_id, auth.org_id)

        # 3. Apply default TTL from project config
        ttl = payload.ttl_seconds or await self._get_project_default_ttl(project_id)

        # 4. Build domain model
        entry = MemoryEntry(
            session_id=payload.session_id,
            agent_id=payload.agent_id,
            key=payload.key,
            value=payload.value,
            tags=payload.tags,
            ttl_seconds=ttl,
            project=project_id,
        )

        # 5. Persist
        stored = await self.repo.store_memory(entry)

        # 6. Cache hot memory (read-through cache)
        await self.cache.set_memory(stored)

        # 7. Meter usage
        await self.metering.record(org_id=auth.org_id, metric="memory_writes", quantity=1)

        # 8. Audit log
        logger.info("Memory created", extra={"memory_id": stored.id, "project_id": project_id})

        return stored


# ── Repository Layer (data access) ──
# File: src/memory_bridge/repository/postgres_repo.py

class PostgresMemoryRepository(MemoryRepository):
    """PostgreSQL asyncpg-backed data access layer."""

    def __init__(self, pool: asyncpg.Pool, tenant_schema: str):
        self.pool = pool
        self.schema = tenant_schema

    async def store_memory(self, entry: MemoryEntry) -> MemoryEntry:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""INSERT INTO {self.schema}.memories
                    (id, session_id, agent_id, key, value, tags,
                     created_at, updated_at, ttl_seconds)
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6::text[],
                            $7, $8, $9)
                    ON CONFLICT (id) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = EXCLUDED.updated_at,
                        tags = EXCLUDED.tags
                    RETURNING *""",
                entry.id, entry.session_id, entry.agent_id,
                entry.key, json.dumps(entry.value), entry.tags,
                entry.created_at, entry.updated_at, entry.ttl_seconds,
            )
            return self._row_to_entry(row)
```

### 3.2 Dependency Injection

```python
# src/memory_bridge/dependencies.py (refactored)

from functools import lru_cache
from .repository.postgres_repo import PostgresMemoryRepository
from .repository.sqlite_repo import SQLiteMemoryRepository
from .services.memory_service import MemoryService
from .services.session_service import SessionService
from .services.handoff_service import HandoffService
from .services.auth_service import AuthService
from .services.metering_service import MeteringService
from .services.cache_service import CacheService
from .services.admin_service import AdminService
from .config import Settings

settings = Settings()  # pydantic-settings

# ── Database Pool ──────────────────────────────────────────
_pool: Optional[asyncpg.Pool] = None

async def get_db_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=settings.pool_min_size,
            max_size=settings.pool_max_size,
        )
    return _pool

# ── Repository ─────────────────────────────────────────────
async def get_repository(project_id: str) -> MemoryRepository:
    tenant_schema = await get_tenant_schema(project_id)
    pool = await get_db_pool()
    return PostgresMemoryRepository(pool=pool, tenant_schema=tenant_schema)

def get_repository_factory() -> RepositoryFactory:
    """Returns a factory that resolves the right repo for a given project."""
    if settings.use_sqlite:
        return SQLiteRepositoryFactory()
    return PostgresRepositoryFactory()

# ── Services ───────────────────────────────────────────────
async def get_memory_service(project_id: str) -> MemoryService:
    repo = await get_repository(project_id)
    cache = CacheService(redis=await get_redis())
    metering = MeteringService(repo=...)
    return MemoryService(repo=repo, cache=cache, metering=metering)
```

### 3.3 Service Classes

| Service | Responsibility | Key Methods |
|---------|---------------|-------------|
| `MemoryService` | CRUD + search + query + lineage | `create_memory()`, `get_memory()`, `query()`, `search()`, `delete()`, `query_lineage()` |
| `SessionService` | Session lifecycle | `create_session()`, `get_session()`, `get_lineage()`, `delete_session_cascade()` |
| `HandoffService` | Agent handoff protocol | `prepare()`, `execute()`, `validate()`, `sanitize()` |
| `AuthService` | Authentication + authorization | `authenticate()`, `authorize_project()`, `get_api_key()` |
| `MeteringService` | Usage tracking + tier limits | `record()`, `check_limits()`, `get_usage()` |
| `CacheService` | Redis caching layer | `get_memory()`, `set_memory()`, `invalidate()`, `get_session()` |
| `AdminService` | Admin operations | `list_users()`, `manage_projects()`, `get_analytics()`, `manage_keys()` |
| `BillingService` | Subscription + billing | `create_subscription()`, `cancel()`, `sync_usage()`, `handle_webhook()` |
| `UserService` | User account management | `register()`, `login()`, `invite()`, `update_profile()` |

---

## 4. Multi-Tenancy Design

### 4.1 Strategy: Schema-per-Tenant

**Decision matrix:**

| Strategy | Pros | Cons | Verdict |
|----------|------|------|---------|
| Row-level (project column) | Simple, single schema | Noisy tenant, hard backup, data bleed risk | Current approach (v0.2) — insufficient for SaaS |
| **Schema-per-tenant** | Strong isolation, per-tenant backup, per-tenant tunable indexes, easy GDPR deletion | Migration complexity, cross-tenant queries hard | ✅ **Chosen** |
| Database-per-tenant | Maximum isolation | Connection pool explosion, no cross-DB queries | Overkill for <100K tenants |

**Tenant resolution flow:**

```
Request → Auth Middleware → Extract API Key → Lookup project_id
→ Lookup tenant_schema from projects table → Inject into request.state
→ Repository instantiated with correct schema prefix
```

**Tenant catalog table** (in `public` schema):

```sql
CREATE TABLE public.projects (
    id            UUID PRIMARY KEY,
    tenant_schema TEXT NOT NULL UNIQUE,    -- 'tenant_' || id_as_hex
    ...
);
```

**Tenant provisioning** (async, in background):

```python
async def provision_tenant(project_id: str):
    """Creates schema and all tenant tables. Called on project creation."""
    schema_name = f"tenant_{project_id.replace('-', '_')}"
    async with pool.acquire() as conn:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
        await conn.execute(f"""
            CREATE TABLE {schema_name}.sessions ( ... );
            CREATE TABLE {schema_name}.memories ( ... );
            -- indexes, FTS config, etc.
        """)
        await conn.execute(
            "UPDATE projects SET tenant_schema = $1 WHERE id = $2",
            schema_name, project_id
        )
```

### 4.2 Row-Level Security (Alternative for Smaller Tenants)

For the `free` and `starter` tiers where schema-per-tenant is overhead, we support a **shared-schema with RLS** fallback:

```sql
ALTER TABLE public.memories ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON public.memories
    USING (project_id = current_setting('app.current_project_id')::UUID);

-- Set per-request:
SELECT set_config('app.current_project_id', 'abc-123', true);
```

**Tier → Strategy mapping:**

| Tier | Strategy | Reason |
|------|----------|--------|
| Free | RLS (shared schema) | Low data volume, cost optimization |
| Starter | RLS (shared schema) | Same |
| Pro | Schema-per-tenant | Strong isolation, backup granularity |
| Enterprise | Schema-per-tenant + dedicated instance | Maximum compliance (SOC2, HIPAA candidate) |

---

## 5. User & Billing System

### 5.1 User Model

```
User
├── id (UUID)
├── email
├── password_hash (bcrypt)
├── name
├── organizations[] (many-to-many via org_members)
│   ├── role: owner | admin | member | viewer
│   └── projects[]
│       ├── tenant_schema
│       ├── api_keys[]
│       └── usage_metrics
```

### 5.2 Authentication Flow

```
1. POST /auth/register → creates user, creates default org + project
2. POST /auth/login → returns JWT (access + refresh tokens)
3. JWT middleware → validates token, injects user_id + org_id into request.state
4. API Key auth → alternative for machine-to-machine

Token format (JWT):
{
  "sub": "user_uuid",
  "org_id": "org_uuid",
  "permissions": ["read", "write"],
  "exp": 1700000000,
  "iat": 1699900000
}
```

### 5.3 Billing System

**Stripe integration architecture:**

```
┌──────────────┐     POST /stripe/webhook     ┌────────────────────┐
│   Stripe     │──────────────────────────────▶│  Webhook Handler  │
│              │                               │  (no auth, verify │
│              │                               │   Stripe-Signature)│
└──────────────┘                               └────────┬───────────┘
                                                        │
                                            ┌───────────┴───────────┐
                                            │  BillingService       │
                                            │  - update_subscription│
                                            │  - record_payment     │
                                            │  - cancel_at_period   │
                                            │  - send_invoice_email │
                                            └───────────┬───────────┘
                                                        │
                                            ┌───────────┴───────────┐
                                            │  Usage Metering       │
                                            │  - aggregate hourly   │
                                            │  - report to Stripe   │
                                            │  - enforce limits     │
                                            └───────────────────────┘
```

**Tier definitions:**

| Tier | Price | Memories | Sessions | Handoffs | Retention | Support |
|------|-------|----------|----------|----------|-----------|---------|
| Free | $0 | 1,000 | 100 | 500/month | 7 days | Community |
| Starter | $29/mo | 50,000 | 5,000 | 10K/month | 30 days | Email |
| Pro | $99/mo | 500,000 | 50,000 | 100K/month | 90 days | Priority |
| Enterprise | Custom | Unlimited | Unlimited | Custom | Custom | SLA + Dedicated |

**Usage metering:** Records are aggregated hourly into `usage_records`, reported to Stripe every billing period via `stripe.UsageRecord.create()`.

**Limit enforcement** happens at the **service layer** (not repository):

```python
class MemoryService:
    async def create_memory(self, ...):
        current = await self.metering.get_count(org_id, "memories")
        max_allowed = TIER_LIMITS[tier]["max_memories"]
        if current >= max_allowed:
            raise HTTPException(
                429,
                detail=f"Memory limit ({max_allowed}) reached. Upgrade at ..."
            )
```

---

## 6. Background Job System

### 6.1 Job Queue Architecture

```
Redis (Valkey)
└── Job Queue (ARQ / RQ)
    ├── cleanup_jobs        → TTL expiration, session garbage collection
    ├── metering_jobs       → Hourly usage aggregation → Stripe report
    ├── email_jobs          → Invitations, invoices, alerts
    ├── stripe_webhook_jobs → Idempotent webhook processing
    ├── tenant_provisioning → New project schema creation
    ├── audit_log_export    → Daily audit log archival
    └── data_retention      → GDPR deletion, tier limit enforcement
```

### 6.2 Job Definitions

```python
# src/memory_bridge/jobs/__init__.py

from arq import create_pool
from .cleanup import cleanup_expired_memories
from .metering import aggregate_hourly_usage
from .email import send_invitation_email
from .stripe_webhooks import process_stripe_event
from .tenant import provision_tenant_schema

class WorkerSettings:
    functions = [
        cleanup_expired_memories,
        aggregate_hourly_usage,
        send_invitation_email,
        process_stripe_event,
        provision_tenant_schema,
    ]
    redis_settings = {"host": "redis", "port": 6379}
    keep_result = 3600          # 1 hour
    max_tries = 3
    timeout = 300               # 5 min per job
```

**Key jobs in detail:**

#### TTL Cleanup (`cleanup_expired_memories`)
```python
async def cleanup_expired_memories(ctx):
    """Runs every 5 minutes. Deletes expired memories across all tenants."""
    projects = await get_all_active_projects()
    for project in projects:
        repo = await get_repository(project.tenant_schema)
        deleted = await repo.delete_expired_memories()
        if deleted:
            logger.info("Cleaned %d expired memories for project %s", deleted, project.id)
```

**Key difference from current:** Uses PostgreSQL `expires_at` indexed column for O(log n) range scan instead of loading all memories and filtering in Python.

#### Hourly Usage Aggregation (`aggregate_hourly_usage`)
```python
async def aggregate_hourly_usage(ctx):
    """Aggregate raw usage events into hourly windows, report to Stripe."""
    async with pool.acquire() as conn:
        # Materialize hourly aggregates
        await conn.execute("""
            INSERT INTO usage_records (organization_id, metric, quantity, window_start, window_end)
            SELECT organization_id, metric, COUNT(*), date_trunc('hour', NOW()), NOW()
            FROM raw_usage_events
            WHERE created_at > date_trunc('hour', NOW()) - INTERVAL '1 hour'
            GROUP BY organization_id, metric
            ON CONFLICT DO NOTHING
        """)
```

### 6.3 Job Scheduler

```yaml
# Cron-based for simplicity (Phase 1), then ARQ scheduler (Phase 2)

jobs:
  - name: cleanup-expired
    schedule: "*/5 * * * *"     # every 5 minutes
    function: cleanup_expired_memories
  - name: aggregate-usage
    schedule: "@hourly"
    function: aggregate_hourly_usage
  - name: sync-stripe-usage
    schedule: "0 */6 * * *"     # every 6 hours
    function: sync_usage_to_stripe
```

---

## 7. Admin API Design

### 7.1 Current Admin Endpoints (v0.2)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/admin/keys` | Create API key |
| GET | `/admin/keys` | List API keys |
| DELETE | `/admin/keys/{key_id}` | Revoke API key |

### 7.2 Proposed Admin API (v1.0)

All admin endpoints require an API key with `admin` permission or a JWT with `role=admin`.

#### User Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/users` | List all users (paginated) |
| GET | `/admin/users/{user_id}` | Get user details |
| PATCH | `/admin/users/{user_id}` | Update user (suspend, change role) |
| DELETE | `/admin/users/{user_id}` | Delete user + GDPR data removal |
| GET | `/admin/users/{user_id}/projects` | List user's projects |

#### Organization Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/orgs` | List all organizations |
| GET | `/admin/orgs/{org_id}` | Get org details + billing info |
| PATCH | `/admin/orgs/{org_id}` | Update org (change tier, suspend) |
| POST | `/admin/orgs/{org_id}/cancel` | Force cancel subscription |
| GET | `/admin/orgs/{org_id}/usage` | Get usage breakdown |

#### Project Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/projects` | List all projects |
| GET | `/admin/projects/{project_id}` | Get project details |
| PATCH | `/admin/projects/{project_id}` | Update limits, config |
| DELETE | `/admin/projects/{project_id}` | Delete project + all tenant data |
| POST | `/admin/projects/{project_id}/migrate` | Trigger schema migration |

#### Analytics & Monitoring

| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/analytics/overview` | Global platform stats |
| GET | `/admin/analytics/usage` | Usage breakdown by tier, org |
| GET | `/admin/analytics/latency` | p50/p95/p99 latency across all |
| GET | `/admin/analytics/errors` | Error rate breakdown |
| GET | `/admin/analytics/active-users` | DAU/WAU/MAU |

#### API Key Management (Enhanced)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/admin/api-keys` | Create key (now with permissions, expiry) |
| GET | `/admin/api-keys` | List keys (all orgs, with filters) |
| GET | `/admin/api-keys/{key_id}` | Get key details |
| PATCH | `/admin/api-keys/{key_id}` | Update key (rotate, change permissions) |
| DELETE | `/admin/api-keys/{key_id}` | Revoke key |

#### System Health (Enhanced — was `/health`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness check (returns 200) |
| GET | `/health/ready` | Readiness check (DB, Redis, queue) |
| GET | `/health/db` | Database connection pool status |
| GET | `/health/redis` | Redis ping check |
| GET | `/health/queue` | Job queue depth, failed jobs |

### 7.3 Admin API Implementation

```python
# src/memory_bridge/controllers/admin_controller.py

router = APIRouter(prefix="/admin", tags=["admin"])

async def require_admin(request: Request):
    """Dependency: ensure the caller has admin privileges."""
    auth = getattr(request.state, "auth", None)
    if not auth or auth.get("role") != "admin":
        raise HTTPException(403, "Admin access required")

@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
    admin_service: AdminService = Depends(get_admin_service),
    _: None = Depends(require_admin),
):
    return await admin_service.list_users(page=page, per_page=per_page, search=search)

@router.get("/analytics/overview")
async def analytics_overview(
    admin_service: AdminService = Depends(get_admin_service),
    _: None = Depends(require_admin),
):
    return {
        "total_users": await admin_service.count_users(),
        "total_orgs": await admin_service.count_organizations(),
        "total_projects": await admin_service.count_projects(),
        "total_memories": await admin_service.count_memories_all(),
        "total_sessions": await admin_service.count_sessions_all(),
        "active_last_24h": await admin_service.count_active_orgs(hours=24),
    }
```

---

## 8. Scaling Strategy

### 8.1 Phase 1: 0–50K Users (Immediate)

**Target:** Single PostgreSQL primary + 2 read replicas, 10 FastAPI workers

```
┌──────────┐    ┌──────────┐    ┌──────────┐
│ Worker 1 │    │ Worker 2 │    │ Worker N │
└────┬─────┘    └────┬─────┘    └────┬─────┘
     ├───────────────┼───────────────┤
     │      asyncpg pool (read-write)│
     └───────────────┼───────────────┘
                     │
          ┌──────────┴──────────┐
          │  PostgreSQL Primary │
          └──────────┬──────────┘
                     │ Streaming replication
          ┌──────────┴──────────┐
          │  Read Replica (x2)  │
          └─────────────────────┘

Redis:
├── Rate limiter buckets (sliding window)
├── Session cache (TTL: 5 min)
├── Job queue (ARQ)
└── Lock manager (handoff session locks)
```

**Key metrics:**
- Connection pool: 10 workers × 10 connections = 100 connections
- PostgreSQL max_connections: 200
- Redis memory: 2GB (holds ~1M cached memories)
- Disk: 500GB SSD (NVMe for WAL + index performance)

**Disk I/O patterns:**
- **Write-heavy:** `memories` table — append-mostly with periodic deletes
- **Read-heavy:** `memories` and `sessions` — point lookups by session_id
- **Index maintenance:** GIN indexes on tags, FTS → vacuum-friendly config
- **WAL sizing:** ~10% of write throughput, tune `wal_buffers` and `checkpoint_segments`

**PostgreSQL tuning:**
```ini
# postgresql.conf
shared_buffers = 4GB                    # 25% of RAM
effective_cache_size = 12GB             # 75% of RAM
work_mem = 64MB
maintenance_work_mem = 512MB
wal_buffers = 64MB
random_page_cost = 1.1                 # SSD
effective_io_concurrency = 200
max_worker_processes = 16
max_parallel_workers_per_gather = 4
max_parallel_workers = 8
autovacuum_vacuum_scale_factor = 0.01  # Aggressive vacuum for TTL deletes
autovacuum_vacuum_threshold = 1000
```

### 8.2 Phase 2: 50K–500K Users (Growth)

**Target:** Read replicas + materialized views + Redis cache layer

```
┌──────────┐    ┌──────────┐    ┌──────────┐
│  Writer   │    │  Reader  │    │  Reader  │
│  Workers  │    │ Workers  │    │ Workers  │
└────┬──────┘    └───┬──────┘    └───┬──────┘
     │               │               │
     │ (write pool)  │ (read pool)   │
     │               ├───────────────┤
     │               │   Redis Cache │
     │               │   (read-      │
     │               │    through)   │
     ▼               ▼               ▼
┌──────────┐    ┌──────────┐    ┌──────────┐
│ PgBouncer│    │ PgBouncer│    │ PgBouncer│
└────┬─────┘    └────┬─────┘    └────┬─────┘
     │               │               │
┌────┴───────────────┴───────────────┴────┐
│         PostgreSQL Primary             │
│  (writes + critical reads)             │
└──────────┬─────────────────────────────┘
           │ Streaming replication
     ┌─────┴──────┐ ┌──────┴──────┐ ┌──────┴──────┐
     │ Replica 1  │ │ Replica 2   │ │ Replica 3   │
     │ (reads)    │ │ (reads)     │ │ (reads)     │
     └────────────┘ └─────────────┘ └─────────────┘
```

**Caching strategy:**

```python
class CacheService:
    """Two-level cache: L1 (local memory) + L2 (Redis)."""

    LOCAL_TTL = 1     # 1 second for hot data
    REDIS_TTL = 300   # 5 minutes

    async def get_memory(self, memory_id: str) -> Optional[MemoryEntry]:
        # L1: Check local dict cache
        entry = self._l1_get(memory_id)
        if entry:
            return entry

        # L2: Check Redis
        data = await self.redis.get(f"mem:{memory_id}")
        if data:
            entry = MemoryEntry.model_validate_json(data)
            self._l1_set(memory_id, entry)
            return entry

        return None

    async def set_memory(self, entry: MemoryEntry):
        # Write-through: write to Redis after DB write
        await self.redis.setex(
            f"mem:{entry.id}",
            self.REDIS_TTL,
            entry.model_dump_json(),
        )
```

**Materialized views:**

```sql
-- Session summary (refreshed every minute)
CREATE MATERIALIZED VIEW session_summary AS
SELECT
    session_id,
    COUNT(*) AS memory_count,
    MAX(updated_at) AS last_activity,
    COUNT(DISTINCT agent_id) AS agent_count
FROM memories
GROUP BY session_id;

-- Hourly usage rollup
CREATE MATERIALIZED VIEW hourly_usage AS
SELECT
    date_trunc('hour', created_at) AS hour,
    project_id,
    COUNT(*) AS writes,
    COUNT(DISTINCT session_id) AS sessions_active
FROM memories
GROUP BY 1, 2;
```

### 8.3 Phase 3: 500K–Millions (Hypergrowth)

**Target:** Shard by project_id, event-driven architecture

```
                         ┌──────────────────┐
                         │  Global Router   │
                         │  (project_id →   │
                         │   shard mapping) │
                         └───┬────┬────┬────┘
                             │    │    │
                  ┌──────────┘    │    └──────────┐
                  ▼               ▼               ▼
          ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
          │   Shard 1    │ │   Shard 2    │ │   Shard N    │
          │ (proj 1-100) │ │(proj 101-200)│ │(proj N*100)  │
          │              │ │              │ │              │
          │  Primary     │ │  Primary     │ │  Primary     │
          │  + 2 replicas│ │  + 2 replicas│ │  + 2 replicas│
          └──────────────┘ └──────────────┘ └──────────────┘
```

**Sharding strategy:**
- **Shard key:** `project_id` (hash → consistent hash ring)
- **256 logical shards** mapped onto 8–32 physical PostgreSQL instances
- **Router:** Application-level `ShardRouter` class reads from a shard map in Redis
- **Re-sharding:** Add shard nodes, rebalance with pg_dump/pg_restore in maintenance window

```python
class ShardRouter:
    """Maps project_id to database shard."""

    def __init__(self):
        self.shards: dict[int, ShardConfig] = {}  # shard_id → connection info
        self.hash_ring: ConsistentHashRing  # 256 virtual nodes

    async def get_shard(self, project_id: str) -> ShardConfig:
        shard_id = self.hash_ring.get_node(project_id)
        return self.shards[shard_id]

    async def get_repository(self, project_id: str) -> MemoryRepository:
        shard = await self.get_shard(project_id)
        pool = await self._get_pool(shard)
        schema = await self._get_tenant_schema(project_id)
        return PostgresMemoryRepository(pool=pool, tenant_schema=schema)
```

**Event-driven components (Phase 3):**

```
Memory Write → Event Bus (Redis Pub/Sub / Kafka)
    ├── Cache Invalidation Worker
    ├── FTS Indexing Worker
    ├── WebSocket Notifier
    ├── Audit Log Writer
    └── Webhook Forwarder (user-configured endpoints)
```

### 8.4 CDN Strategy

```
User → Cloudflare → ALB → FastAPI Worker

Cacheable responses:
├── GET /memories/{id}          → Cache-Control: private, max-age=5
├── GET /sessions/{id}          → Cache-Control: private, max-age=5
├── GET /health                 → Cache-Control: no-cache (bypass)
├── GET /health/ready           → Cache-Control: no-cache
├── GET /analytics/*            → Cache-Control: no-cache
└── GET /docs, /openapi.json    → Cache-Control: public, max-age=3600
```

---

## 9. File Manifest

### 9.1 New Files to Create

```
src/memory_bridge/
├── config.py                          # Pydantic Settings class (env → config)
├── dependencies.py                    # NEW: DI container, pool management, factories
├── repository/
│   ├── __init__.py                    # MemoryRepository ABC/protocol
│   ├── base.py                        # Abstract base + common utilities
│   ├── sqlite_repo.py                 # SQLite implementation (from current storage.py)
│   └── postgres_repo.py              # asyncpg implementation
├── services/
│   ├── __init__.py
│   ├── memory_service.py             # Memory business logic
│   ├── session_service.py            # Session business logic + cascade deletes
│   ├── handoff_service.py            # Handoff orchestration (from current handoff.py)
│   ├── auth_service.py               # Auth + API key management
│   ├── admin_service.py              # Admin operations
│   ├── billing_service.py            # Stripe integration
│   ├── metering_service.py           # Usage tracking + tier limits
│   ├── cache_service.py              # Redis caching
│   └── user_service.py               # User registration, login, invites
├── controllers/
│   ├── __init__.py
│   ├── memory_controller.py          # /memories endpoints
│   ├── session_controller.py         # /sessions endpoints
│   ├── handoff_controller.py         # /handoff endpoints
│   ├── admin_controller.py           # /admin endpoints
│   ├── auth_controller.py            # /auth endpoints (register, login)
│   ├── billing_controller.py         # /stripe webhook, /billing endpoints
│   └── health_controller.py          # /health, /health/ready
├── middleware/
│   ├── __init__.py
│   ├── auth.py                       # Auth middleware (updated with JWT + API key)
│   ├── rate_limit.py                 # Redis-backed rate limiter
│   ├── request_id.py                 # Request ID middleware (from main.py)
│   └── tenant.py                     # Tenant schema resolver
├── models/
│   ├── __init__.py
│   ├── memory.py                     # MemoryEntry, MemoryCreate, MemoryQuery, MemorySearchResult
│   ├── session.py                    # Session model
│   ├── handoff.py                    # HandoffPayload, HandoffResult
│   ├── user.py                       # User, Organization, Project models
│   ├── billing.py                    # Subscription, UsageRecord models
│   └── api_key.py                    # APIKey model
├── jobs/
│   ├── __init__.py                   # ARQ WorkerSettings
│   ├── cleanup.py                    # TTL cleanup job
│   ├── metering.py                   # Usage aggregation job
│   ├── email.py                      # Email sending jobs
│   ├── stripe_webhooks.py            # Stripe webhook processing
│   ├── tenant.py                     # Tenant provisioning
│   └── audit.py                      # Audit log export
├── migrations/
│   ├── __init__.py
│   ├── base.py                       # Migration base class
│   ├── runner.py                     # Migration runner (dual-backend)
│   ├── schema_version.py             # Schema version tracking
│   ├── sqlite/                       # SQLite migration SQL files
│   │   ├── 001_initial.sql
│   │   └── 002_add_handoffs.sql
│   └── postgresql/                   # PostgreSQL migration SQL files
│       ├── 001_initial.sql
│       └── 002_add_handoffs.sql
├── auth.py                           # REMOVED → split into middleware/auth.py + services/auth_service.py
├── cli.py                            # UPDATED: add admin CLI commands
├── main.py                           # REWRITTEN: thin app factory, imports controllers
├── metrics.py                        # UPDATED: more metrics (per-endpoint, per-tenant)
├── ratelimit.py                      # REMOVED → replaced by middleware/rate_limit.py
├── storage.py                        # REMOVED → split into repository/*.py
└── __init__.py                       # UPDATED version

src/memory_bridge_client/
├── client.py                         # UPDATED: add auth, project, billing methods
└── __init__.py

tests/
├── unit/
│   ├── test_memory_service.py
│   ├── test_session_service.py
│   ├── test_handoff_service.py
│   ├── test_auth_service.py
│   ├── test_metering_service.py
│   ├── test_billing_service.py
│   ├── test_cache_service.py
│   └── test_admin_service.py
├── integration/
│   ├── conftest.py                   # Test fixtures (SQLite test DB, Redis mock)
│   ├── test_memory_repo.py           # Repository contract tests (run against both backends)
│   ├── test_session_repo.py
│   ├── test_api_endpoints.py         # Full HTTP integration tests
│   └── test_jobs.py                  # Job execution tests
├── e2e/
│   ├── test_full_flow.py
│   └── test_multi_tenant.py

docs/
├── saas-architecture.md              # THIS DOCUMENT
├── api-reference.md                  # Full API reference (auto-generated)
├── deployment.md                     # Production deployment guide
└── development.md                    # Local dev setup

docker-compose.yml                    # NEW: PostgreSQL, Redis, app, worker services
Dockerfile                            # UPDATED: multi-stage with PostgreSQL driver
pyproject.toml                        # UPDATED: add asyncpg, redis, arq, stripe, pydantic-settings
```

### 9.2 Files to Modify

| File | Change |
|------|--------|
| `src/memory_bridge/main.py` | Rewrite: app factory pattern, register routers, use DI |
| `src/memory_bridge/dependencies.py` | Rewrite: full DI container with pool lifecycle |
| `src/memory_bridge/models.py` | Split into models/ package |
| `src/memory_bridge/metrics.py` | Add per-endpoint, per-tenant metrics |
| `src/memory_bridge/auth.py` | Remove file — logic moved to middleware/ + services/ |
| `src/memory_bridge/storage.py` | Remove file — logic split into repository/ |
| `src/memory_bridge/ratelimit.py` | Remove file — replaced by Redis-backed in middleware/ |
| `pyproject.toml` | Add new dependencies |
| `Dockerfile` | Add libpq, asyncpg driver |

---

## 10. Priority-Ordered Execution Phases

### Phase 0: Foundation (Week 1–2)

**Goal:** Pluggable storage backend, no breaking API changes.

| # | Task | Owner | Files |
|---|------|-------|-------|
| 0.1 | Define `MemoryRepository` ABC | Henry (Architect) | `repository/__init__.py` |
| 0.2 | Extract `SQLiteMemoryRepository` from current `storage.py` | Fred (Executor) | `repository/sqlite_repo.py` |
| 0.3 | Implement `PostgresMemoryRepository` | Fred | `repository/postgres_repo.py` |
| 0.4 | Add Pydantic `Settings` class | Henry | `config.py` |
| 0.5 | Rewrite `dependencies.py` with factory pattern | Henry | `dependencies.py` |
| 0.6 | Write migration runner (dual-backend) | Fred | `migrations/runner.py` |
| 0.7 | Create PG schema migration SQL | Henry | `migrations/postgresql/*.sql` |
| 0.8 | Add `asyncpg`, `pydantic-settings` to pyproject.toml | Fred | `pyproject.toml` |
| 0.9 | Create docker-compose.yml (pg + redis + app) | Fred | `docker-compose.yml` |
| 0.10 | Repository contract tests (both backends) | Both | `tests/integration/test_memory_repo.py` |

### Phase 1: Service Layer (Week 3–4)

**Goal:** Business logic extracted from controllers. No monolithic storage class.

| # | Task | Owner | Files |
|---|------|-------|-------|
| 1.1 | Create `MemoryService` | Fred | `services/memory_service.py` |
| 1.2 | Create `SessionService` | Fred | `services/session_service.py` |
| 1.3 | Create `HandoffService` (refactor from current handoff.py) | Fred | `services/handoff_service.py` |
| 1.4 | Create `CacheService` (Redis read-through) | Fred | `services/cache_service.py` |
| 1.5 | Refactor `main.py` into controllers/ | Henry | `controllers/*.py`, `main.py` |
| 1.6 | Add Redis-backed rate limiter | Fred | `middleware/rate_limit.py` |
| 1.7 | Add structured logging with `structlog` | Henry | `middleware/logging.py` |
| 1.8 | Unit tests for all services | Both | `tests/unit/test_*_service.py` |

### Phase 2: Multi-Tenancy (Week 5–6)

**Goal:** Schema-per-tenant, tenant provisioning, project isolation.

| # | Task | Owner | Files |
|---|------|-------|-------|
| 2.1 | Create `public.projects` table + tenant catalog | Henry | `migrations/postgresql/` |
| 2.2 | Implement `TenantResolver` middleware | Fred | `middleware/tenant.py` |
| 2.3 | Create tenant provisioning job | Fred | `jobs/tenant.py` |
| 2.4 | Update `APIKeyMiddleware` for JWT + API key dual-auth | Henry | `middleware/auth.py` |
| 2.5 | Add project_id inference from API key | Henry | `services/auth_service.py` |
| 2.6 | Update all controllers to pass project context | Fred | `controllers/*.py` |
| 2.7 | RLS fallback for free tier | Henry | `repository/postgres_repo.py` |
| 2.8 | Multi-tenant integration tests | Both | `tests/integration/test_multi_tenant.py` |

### Phase 3: User & Billing (Week 7–8)

**Goal:** Registration, login, Stripe subscriptions, usage metering.

| # | Task | Owner | Files |
|---|------|-------|-------|
| 3.1 | Create `UserService` (register, login, JWT) | Fred | `services/user_service.py` |
| 3.2 | Create auth endpoints (`/auth/register`, `/auth/login`) | Fred | `controllers/auth_controller.py` |
| 3.3 | Create `BillingService` (Stripe integration) | Henry | `services/billing_service.py` |
| 3.4 | Create Stripe webhook handler | Henry | `controllers/billing_controller.py` |
| 3.5 | Create `MeteringService` (usage tracking) | Fred | `services/metering_service.py` |
| 3.6 | Tier limit enforcement in services | Henry | `services/memory_service.py` |
| 3.7 | Create usage aggregation job | Fred | `jobs/metering.py` |
| 3.8 | Create email sending job | Fred | `jobs/email.py` |

### Phase 4: Admin & Observability (Week 9–10)

**Goal:** Admin dashboard API, Prometheus + Grafana, structured logging.

| # | Task | Owner | Files |
|---|------|-------|-------|
| 4.1 | Create `AdminService` | Henry | `services/admin_service.py` |
| 4.2 | Create admin controllers | Fred | `controllers/admin_controller.py` |
| 4.3 | Add per-endpoint Prometheus metrics | Henry | `metrics.py` |
| 4.4 | Add health check endpoints | Fred | `controllers/health_controller.py` |
| 4.5 | Add structured logging (request ID, tenant, latency) | Henry | `middleware/logging.py` |
| 4.6 | Add OpenTelemetry tracing | Henry | `middleware/tracing.py` |
| 4.7 | Create Grafana dashboards | Fred | `docs/grafana/dashboards/` |
| 4.8 | Create Prometheus alerting rules | Henry | `docs/prometheus/alerts.yml` |

### Phase 5: Scale to Production (Week 11–12)

**Goal:** Read replicas, connection pooling, load testing, hardening.

| # | Task | Owner | Files |
|---|------|-------|-------|
| 5.1 | Add PgBouncer connection pooling | Fred | `docker-compose.yml` |
| 5.2 | Separate read/write pool in repository | Henry | `repository/postgres_repo.py` |
| 5.3 | Implement materialized views for analytics | Henry | `migrations/postgresql/` |
| 5.4 | Load testing with locust/k6 | Both | `tests/load/locustfile.py` |
| 5.5 | Connection leak detection + graceful pool drain | Fred | `dependencies.py` |
| 5.6 | Add database migration CI (run on deploy) | Henry | `.github/workflows/` |
| 5.7 | Production Dockerfile + HEALTHCHECK | Fred | `Dockerfile` |
| 5.8 | Document deployment runbook | Henry | `docs/deployment.md` |

### Phase 6: Hypergrowth (Week 13–16)

**Goal:** Sharding, event-driven architecture, CDN optimization.

| # | Task | Owner | Files |
|---|------|-------|-------|
| 6.1 | Implement `ShardRouter` | Henry | `repository/shard_router.py` |
| 6.2 | Add consistent hash ring | Henry | `repository/hash_ring.py` |
| 6.3 | Implement event bus abstraction | Fred | `events/event_bus.py` |
| 6.4 | Add webhook forwarding for events | Fred | `events/webhook.py` |
| 6.5 | Add S3 offloading for large memory values | Fred | `repository/s3_store.py` |
| 6.6 | CDN caching configuration | Henry | `middleware/cache_headers.py` |
| 6.7 | Re-sharding tooling | Both | `jobs/rebalance.py` |
| 6.8 | Chaos engineering tests | Both | `tests/chaos/` |

---

## Appendix A: Current Architecture Limitations Map

| Limitation | Impact | Fix in SaaS |
|-----------|--------|-------------|
| Single-file SQLite | Single-writer bottleneck, no replication | PostgreSQL + asyncpg pool + read replicas |
| Module-level singleton | No lifecycle, no pooling, leaky | DI factory with pool lifecycle management |
| In-memory rate limiter | Lost on restart, not shared across workers | Redis-backed sliding window |
| Single uvicorn process | No horizontal scaling | Gunicorn + multiple workers + LB |
| No user accounts | No onboarding, no orgs, no billing | UserService + JWT + Stripe |
| No Web UI | No visibility, no debug tools | Separate frontend (not in scope for this architecture) |
| Monolithic storage class | 751 lines, no separation of concerns | Repository/Service/Controller split |
| No background jobs | TTL runs inline, no metering | ARQ job queue in dedicated workers |

## Appendix B: Key Metrics & SLA Targets

| Metric | Target | Measurement |
|--------|--------|-------------|
| P50 memory write latency | < 10ms | Prometheus histogram |
| P95 memory write latency | < 50ms | Prometheus histogram |
| P99 memory write latency | < 200ms | Prometheus histogram |
| Memory read (cache hit) | < 2ms | Prometheus + cache hit ratio |
| Memory read (cache miss) | < 20ms | Prometheus histogram |
| Availability (SLA) | 99.9% uptime | Uptime check every 30s |
| Max connections per worker | 20 | asyncpg pool stats |
| Max connections total | 200 (10 workers × 20) | PgBouncer stats |
| DB size per tenant (Pro) | < 50GB | Usage tracking |
| Redis memory | < 80% of allocated | Redis INFO memory |
| Job queue backlog | < 1000 | ARQ queue depth |
