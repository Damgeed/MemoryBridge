# Memory Bridge SaaS Execution Plan

> **Author:** Fred 🚀 — The Executor / Co-founder
> **Synthesized from:** Henry 🧠 (Architecture), Rex 🔒 (Security Audit), Nova 🌟 (Vision)
> **Date:** May 23, 2026
> **Target:** v0.2.0 → v1.0 SaaS Launch
> **Constraint:** All existing tests must pass after every phase. SQLite backend must continue working for self-hosted users.

---

## Table of Contents

1. [Prioritized Phase Map](#section-1-prioritized-phase-map)
2. [Phase-by-Phase Execution](#section-2-phase-by-phase-execution)
3. [Gate Criteria](#section-3-gate-criteria)
4. [Risk Register](#section-4-risk-register)
5. [Quick Wins (Day 1)](#section-5-quick-wins-day-1)

---

## Section 1: Prioritized Phase Map

### Priority Ordering Rationale

Per our constraints: **Security > Data Integrity > Performance > Features**

Rex's critical issues are distributed across phases based on dependency ordering — we can't fix tenant isolation (C3) until we have multi-tenancy infrastructure, but we CAN fix open-mode bypass (C1) and request size limits (C5) on Day 1.

### Consolidated Phase List

| # | Phase | Objective | Fixes Rex Issues | Unlocks Nova Vision | Effort |
|---|-------|-----------|-----------------|---------------------|--------|
| **0** | **Quick Wins & Security Hotfixes** | Patch immediate security holes before any refactoring | C1, C2 (partial), C5, C9 | — | **Low** |
| **1** | **Storage Refactor (Repository Pattern)** | Pluggable dual-backend (SQLite + PostgreSQL), no API breakage | C6 (SQLite single-writer), C7 (race conditions) | PostgreSQL backend (Tier 1 table stake) | **High** |
| **2** | **Service Layer & Auth 2.0** | Business logic extracted; Redis-backed rate limiting; proper auth hashing | C8 (in-memory rate limiter), C2 (proper bcrypt) | Auth system, API keys with permissions | **High** |
| **3** | **Multi-Tenancy & Project Isolation** | Schema-per-tenant, tenant provisioning, project isolation | C3 (no tenant isolation), C4 (admin API auth) | Project isolation, multi-project support | **High** |
| **4** | **User System & Billing** | Registration, login, JWT, Stripe subscriptions, usage metering | C10 (Prometheus single-process — fixed in Phase 5) | Free tier (100MB, 10K queries/day), Pro upgrades | **High** |
| **5** | **Admin & Observability** | Admin dashboard API, Prometheus multi-process, Grafana, structured logging | C10 | Dashboard, health panel, observability | **Medium** |
| **6** | **Scale to Production** | Read replicas, PgBouncer, load testing, deployment runbook | — | 99.9% SLA, production readiness | **High** |
| **7** | **Hypergrowth** | Sharding, event-driven architecture, CDN, S3 offloading | — | Enterprise scale, Nova's flywheel | **Medium** |

### Dependency Graph

```
Phase 0 (Quick Wins)
    │
    ▼
Phase 1 (Storage Refactor) ─── SQLite must keep working
    │
    ▼
Phase 2 (Service Layer + Auth) ─── Unlocks Redis rate limiting
    │
    ▼
Phase 3 (Multi-Tenancy) ─── Unlocks project isolation
    │
    ▼
Phase 4 (User + Billing) ─── Unlocks free tier / SaaS signups
    │
    ├──▶ Phase 5 (Admin + Observability) ─── can be parallel
    │
    ▼
Phase 6 (Scale to Production)
    │
    ▼
Phase 7 (Hypergrowth)
```

---

## Section 2: Phase-by-Phase Execution

---

### Phase 0: Quick Wins & Security Hotfixes

**Objective:** Patch immediate security vulnerabilities with <30-minute changes. These are the fixes Rex flagged that don't require architectural changes.

**Fixes:** C1 (open mode bypass), C2 (API key hashing), C5 (request size limits), C9 (metrics endpoint leak)

**Estimated effort:** Low (half a day)

#### Task 0.1 — Fix Open Mode Bypass (Fixes C1)

**Objective:** When `MEMORY_BRIDGE_API_KEY` env var is not set and no DB keys exist, the middleware operates in "open mode" — **every request is unauthenticated**. This is a critical production vulnerability. Change the default to REQUIRE auth and log a warning.

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/auth.py` (line 50-52)

**Change:** Replace the silent open-mode fallback with explicit opt-in via `MEMORY_BRIDGE_ALLOW_OPEN=true` env var.

```python
# BEFORE (line 50-52):
if not env_key and not self._has_db_keys:
    # Open mode — no auth configured
    return await call_next(request)

# AFTER:
if not env_key and not self._has_db_keys:
    open_mode = os.environ.get("MEMORY_BRIDGE_ALLOW_OPEN", "false").lower() == "true"
    if not open_mode:
        logger.warning(
            "🚨 SECURITY: No API keys configured and MEMORY_BRIDGE_ALLOW_OPEN is not set. "
            "All requests will be rejected. Set MEMORY_BRIDGE_API_KEY or MEMORY_BRIDGE_ALLOW_OPEN=true (dev only)."
        )
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required. No API keys configured on server. "
                     "Set MEMORY_BRIDGE_API_KEY environment variable or create an admin key."},
        )
    # Dev open mode — warn loudly
    logger.warning("  OPEN MODE: No auth configured. Set MEMORY_BRIDGE_API_KEY for production.")
    return await call_next(request)
```

**Dependencies:** None
**Verification:** Start server without `MEMORY_BRIDGE_API_KEY` — requests should return 401 instead of succeeding silently.

#### Task 0.2 — Fix SHA-256 Key Hashing (Fixes C2, partial)

**Objective:** Replace ad-hoc SHA-256 hashing for API keys with proper bcrypt hashing. Current `storage.py` uses SHA-256 which is fast enough for brute-force. Switch to `bcrypt` or `hashlib.scrypt`.

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/storage.py` (search for `hashlib.sha256` or similar)
- `~/MemoryBridge/pyproject.toml` (add `bcrypt` dependency)

**Change:** Replace SHA-256 with bcrypt for API key hashing.

**Dependencies:** None
**Verification:** Create a new API key — the stored hash should be a bcrypt hash (starts with `$2b$`). Authentication should still work.

#### Task 0.3 — Add Request Size Limits (Fixes C5)

**Objective:** FastAPI doesn't limit request body size by default. Add a 10MB max body size to prevent memory exhaustion attacks.

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/main.py`

**Change:** Add `max_request_size` middleware or configure the ASGI server.

```python
# Add near the top of main.py, after app creation:
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

MAX_BODY_SIZE = int(os.environ.get("MEMORY_BRIDGE_MAX_BODY_SIZE", "10_485_760"))  # 10MB default

class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_BODY_SIZE:
            return Response(
                status_code=413,
                content=f'{{"detail":"Request body exceeds {MAX_BODY_SIZE} byte limit"}}',
                media_type="application/json",
            )
        return await call_next(request)

app.add_middleware(RequestSizeLimitMiddleware)
```

**Dependencies:** None
**Verification:** Send a POST with `Content-Length: 10485761` — should get 413.

#### Task 0.4 — Restrict Metrics Endpoint (Fixes C9)

**Objective:** The `/metrics` endpoint exposes Prometheus data including memory/session counts and request rates. While not sensitive per se, it's information leakage and should require admin-level auth in production.

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/auth.py` (add `/metrics` to exempt paths — BUT: add auth to the metrics endpoint itself)
- `~/MemoryBridge/src/memory_bridge/main.py` (add auth check to `/metrics` handler)

**Change:** Remove `/metrics` from exempt paths and add a separate flag:
```python
# In main.py, at the metrics endpoint:
@router.get("/metrics")
async def metrics(
    storage: MemoryStorage = Depends(get_storage),
    request: Request,
):
    # Require auth unless MEMORY_BRIDGE_PUBLIC_METRICS=true
    if os.environ.get("MEMORY_BRIDGE_PUBLIC_METRICS", "").lower() != "true":
        auth = getattr(request.state, "auth", None)
        if not auth:
            raise HTTPException(status_code=401, detail="Authentication required for /metrics")
    ...
```

**Dependencies:** None
**Verification:** Metrics endpoint should return 401 without auth header by default.

---

### Phase 1: Storage Refactor (Repository Pattern)

**Objective:** Extract the monolithic `MemoryStorage` class into an abstract `MemoryRepository` ABC with `SQLiteMemoryRepository` and `PostgresMemoryRepository` implementations. This is the foundation for everything else.

**Fixes Rex Issues:** C6 (SQLite single-writer → PostgreSQL scales), C7 (race conditions in storage metrics)

**Unlocks Nova Vision:** PostgreSQL backend (Tier 1 table stake — "Developers can use it in production")

**Estimated effort:** High (2 weeks)

#### Task 1.1 — Define `MemoryRepository` ABC

**Objective:** Create the abstract base class that both backends implement.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/repository/__init__.py`

```python
"""Abstract repository interface for memory storage backends."""
from abc import ABC, abstractmethod
from typing import Optional
from ..models import MemoryEntry, Session, MemoryQuery

class MemoryRepository(ABC):
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
    async def search_memories(self, query: str, **filters) -> list[MemoryEntry]: ...
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
    @abstractmethod
    async def create_api_key(self, label: str, **kwargs) -> dict: ...
    @abstractmethod
    async def authenticate_key(self, key: str) -> Optional[dict]: ...
    @abstractmethod
    async def list_api_keys(self) -> list[dict]: ...
    @abstractmethod
    async def revoke_api_key(self, key_id: str) -> bool: ...
```

**Files to create (continued):**
- `~/MemoryBridge/src/memory_bridge/repository/base.py` — Common utilities (row-to-model mappers, JSON helpers)

**Dependencies:** Phase 0 complete
**Verification:** ABC can be imported without errors.

#### Task 1.2 — Extract `SQLiteMemoryRepository`

**Objective:** Refactor current `storage.py` (751 lines) into a clean `SQLiteMemoryRepository` class implementing `MemoryRepository`. Keep ALL existing functionality. The SQLite backend must continue to work for self-hosted users.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/repository/sqlite_repo.py`

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/storage.py` — **Deprecate but keep as wrapper** for backward compatibility, or remove and update all imports.

**Key changes:**
- Extract the 751-line monolith into a class inheriting from `MemoryRepository`
- Fix the storage metrics race condition (C7): use `increment_metric` with `RETURNING` or atomic UPSERT instead of read-modify-write
- Maintain all existing method signatures

**Dependencies:** Task 1.1
**Verification:** All existing storage tests pass. `SQLiteMemoryRepository` passes the same contract tests.

#### Task 1.3 — Implement `PostgresMemoryRepository`

**Objective:** Create asyncpg-backed PostgreSQL implementation of `MemoryRepository`. This is the production backend.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/repository/postgres_repo.py`

**Structure:**
```python
class PostgresMemoryRepository(MemoryRepository):
    def __init__(self, pool: asyncpg.Pool, tenant_schema: str = "public"):
        self.pool = pool
        self.schema = tenant_schema

    async def initialize(self):
        # Run migrations against public schema
        ...

    async def store_memory(self, entry: MemoryEntry) -> MemoryEntry:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""INSERT INTO {self.schema}.memories (...) VALUES (...)
                    ON CONFLICT (id) DO UPDATE SET ...
                    RETURNING *""",
                ...
            )
            return self._row_to_entry(row)
```

**Dependencies:** Task 1.1, Task 1.4 (config), Task 1.7 (migrations)
**Verification:** Repository contract tests pass against both SQLite and PostgreSQL.

#### Task 1.4 — Add Pydantic Settings Class

**Objective:** Move from env-var soup to a Pydantic `Settings` class for all configuration.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/config.py`

```python
from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    # Database
    database_url: str = "sqlite:///memory_bridge.db"
    pool_min_size: int = 5
    pool_max_size: int = 20
    pool_max_queries: int = 50000
    pool_max_inactive_connection_lifetime: float = 300.0
    command_timeout: int = 30

    # Auth
    api_key: str = ""
    allow_open: bool = False
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # Rate limiting
    rate_limit_per_minute: int = 60
    rate_limit_backend: str = "memory"  # "memory" | "redis"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Features
    use_sqlite: bool = True  # False = use PostgreSQL
    public_metrics: bool = False
    max_body_size: int = 10_485_760
    cleanup_interval: int = 300
    default_ttl: int = 0

    model_config = {"env_prefix": "MEMORY_BRIDGE_"}

@lru_cache()
def get_settings() -> Settings:
    return Settings()
```

**Dependencies:** Task 1.1
**Verification:** `Settings()` reads from environment variables correctly.

#### Task 1.5 — Rewrite Dependencies with Factory Pattern

**Objective:** Replace the module-level singleton pattern with a proper DI container that manages pool lifecycle.

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/dependencies.py` (rewrite)

```python
class RepositoryFactory:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._pool = None
        self._sqlite_repo = None

    async def get_repository(self, tenant_schema: str = "public") -> MemoryRepository:
        if self.settings.use_sqlite:
            if self._sqlite_repo is None:
                self._sqlite_repo = SQLiteMemoryRepository(self.settings.database_url)
                await self._sqlite_repo.initialize()
            return self._sqlite_repo
        else:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(
                    dsn=self.settings.database_url,
                    min_size=self.settings.pool_min_size,
                    max_size=self.settings.pool_max_size,
                )
            return PostgresMemoryRepository(pool=self._pool, tenant_schema=tenant_schema)

    async def close(self):
        if self._pool:
            await self._pool.close()
```

**Dependencies:** Task 1.2, 1.3, 1.4
**Verification:** Services can be instantiated with either backend via the factory.

#### Task 1.6 — Add Dependencies to pyproject.toml

**Objective:** Add required production dependencies.

**Files to modify:**
- `~/MemoryBridge/pyproject.toml`

**Add to `dependencies`:**
```toml
"asyncpg>=0.29.0",
"pydantic-settings>=2.1.0",
"bcrypt>=4.1.0",
```

**Dependencies:** None
**Verification:** `pip install -e "."` succeeds.

#### Task 1.7 — Create Migration Runner (Dual-Backend)

**Objective:** Unified migration system that works for both SQLite and PostgreSQL. Each migration has a `sql_sqlite` and `sql_pg` version.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/migrations/__init__.py`
- `~/MemoryBridge/src/memory_bridge/migrations/base.py`
- `~/MemoryBridge/src/memory_bridge/migrations/runner.py`
- `~/MemoryBridge/src/memory_bridge/migrations/schema_version.py`
- `~/MemoryBridge/src/memory_bridge/migrations/sqlite/001_initial.sql`
- `~/MemoryBridge/src/memory_bridge/migrations/sqlite/002_add_handoffs.sql`
- `~/MemoryBridge/src/memory_bridge/migrations/postgresql/001_initial.sql`
- `~/MemoryBridge/src/memory_bridge/migrations/postgresql/002_add_handoffs.sql`

**Dependencies:** Task 1.1
**Verification:** Migration runner creates all tables in both backends.

#### Task 1.8 — Create docker-compose.yml

**Objective:** Local development environment with PostgreSQL, Redis, and the app.

**Files to create:**
- `~/MemoryBridge/docker-compose.yml`

```yaml
version: "3.9"
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: memory_bridge
      POSTGRES_USER: mb
      POSTGRES_PASSWORD: mb_dev
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: valkey/valkey:8-alpine
    ports:
      - "6379:6379"

  app:
    build: .
    environment:
      MEMORY_BRIDGE_DATABASE_URL: postgres://mb:mb_dev@postgres/memory_bridge
      MEMORY_BRIDGE_USE_SQLITE: "false"
      MEMORY_BRIDGE_REDIS_URL: redis://redis:6379
    ports:
      - "8000:8000"
    depends_on:
      - postgres
      - redis

volumes:
  pgdata:
```

**Dependencies:** Task 1.5
**Verification:** `docker compose up` starts all services and app responds at `localhost:8000`.

#### Task 1.9 — Repository Contract Tests (Both Backends)

**Objective:** Parameterized tests that run the same assertions against both `SQLiteMemoryRepository` and `PostgresMemoryRepository` to ensure API compatibility.

**Files to create:**
- `~/MemoryBridge/tests/integration/conftest.py` — Test fixtures for both backends
- `~/MemoryBridge/tests/integration/test_memory_repo.py` — Contract tests
- `~/MemoryBridge/tests/integration/test_session_repo.py` — Session contract tests

**Dependencies:** Tasks 1.2, 1.3, 1.7
**Verification:** `pytest tests/integration/ -v` shows all tests passing against both backends.

---

### Phase 2: Service Layer & Auth 2.0

**Objective:** Extract business logic from controllers into service classes. Replace in-memory rate limiter with Redis-backed. Fix API key hashing with bcrypt.

**Fixes Rex Issues:** C8 (in-memory rate limiter), C2 (bcrypt hashing completed)

**Unlocks Nova Vision:** Proper auth system, API keys with granular permissions

**Estimated effort:** High (2 weeks)

#### Task 2.1 — Create `MemoryService`

**Objective:** Extract memory business logic from the controller into a dedicated service class.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/services/__init__.py`
- `~/MemoryBridge/src/memory_bridge/services/memory_service.py`

```python
class MemoryService:
    def __init__(self, repo: MemoryRepository, cache: Optional[CacheService] = None, metering: Optional[MeteringService] = None):
        self.repo = repo
        self.cache = cache
        self.metering = metering

    async def create_memory(self, payload: MemoryCreate, auth: AuthContext) -> MemoryEntry:
        # 1. Resolve project from auth
        # 2. Check tier limits
        # 3. Apply default TTL
        # 4. Create entry
        # 5. Cache hot memory
        # 6. Meter usage
        ...
```

**Dependencies:** Phase 1 complete
**Verification:** All memory operations work through `MemoryService` with the same behavior.

#### Task 2.2 — Create `SessionService`

**Objective:** Session lifecycle management including cascade deletes and lineage queries.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/services/session_service.py`

**Dependencies:** Task 2.1 (for pattern)
**Verification:** Session creation, retrieval, lineage, and cascade delete work.

#### Task 2.3 — Create `HandoffService`

**Objective:** Refactor handoff logic from `handoff.py` into a service class.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/services/handoff_service.py`

**Dependencies:** Task 2.1
**Verification:** All handoff tests pass through the new service.

#### Task 2.4 — Create `CacheService`

**Objective:** Redis-backed read-through cache for hot memories.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/services/cache_service.py`

**Dependencies:** Task 1.4 (Redis config)
**Verification:** Cache hits return data within 2ms. Cache misses fall through to repo.

#### Task 2.5 — Refactor Controllers into `/controllers/`

**Objective:** Break `main.py` (426 lines) into dedicated controller files with routers.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/controllers/__init__.py`
- `~/MemoryBridge/src/memory_bridge/controllers/memory_controller.py`
- `~/MemoryBridge/src/memory_bridge/controllers/session_controller.py`
- `~/MemoryBridge/src/memory_bridge/controllers/handoff_controller.py`

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/main.py` — Thin app factory that imports routers

```python
# main.py becomes:
from fastapi import FastAPI
from .controllers import memory_controller, session_controller, handoff_controller

def create_app() -> FastAPI:
    app = FastAPI(title="Memory Bridge", version="0.3.0")
    app.include_router(memory_controller.router)
    app.include_router(session_controller.router)
    app.include_router(handoff_controller.router)
    return app

app = create_app()
```

**Dependencies:** Tasks 2.1-2.4
**Verification:** All existing API endpoint tests pass with the new controller structure.

#### Task 2.6 — Add Redis-Backed Rate Limiter (Fixes C8)

**Objective:** Replace in-memory `RateLimiter` with a Redis-backed sliding window.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/middleware/__init__.py`
- `~/MemoryBridge/src/memory_bridge/middleware/rate_limit.py`

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/ratelimit.py` — Deprecate/remove

```python
class RedisRateLimiter:
    def __init__(self, redis, requests_per_minute: int = 60):
        self.redis = redis
        self.rate = requests_per_minute

    async def check(self, key: str) -> bool:
        now = int(time.time())
        window_key = f"ratelimit:{key}:{now // 60}"
        count = await self.redis.incr(window_key)
        if count == 1:
            await self.redis.expire(window_key, 120)  # 2x window safety
        return count <= self.rate
```

**Dependencies:** Task 1.4 (Redis config)
**Verification:** Rate limits persist across server restarts. Multiple workers share the same limit counter.

#### Task 2.7 — Unit Tests for All Services

**Objective:** Comprehensive unit tests for each service class.

**Files to create:**
- `~/MemoryBridge/tests/unit/test_memory_service.py`
- `~/MemoryBridge/tests/unit/test_session_service.py`
- `~/MemoryBridge/tests/unit/test_handoff_service.py`
- `~/MemoryBridge/tests/unit/test_cache_service.py`

**Dependencies:** Tasks 2.1-2.6
**Verification:** `pytest tests/unit/ -v` shows all tests passing.

---

### Phase 3: Multi-Tenancy & Project Isolation

**Objective:** Schema-per-tenant with tenant provisioning. Project isolation through the entire stack.

**Fixes Rex Issues:** C3 (no tenant isolation), C4 (admin API auth)

**Unlocks Nova Vision:** Multi-project support, project-level API keys

**Estimated effort:** High (2 weeks)

#### Task 3.1 — Create Public Schema Tables

**Objective:** Create `public.users`, `public.organizations`, `public.projects`, `public.api_keys` tables in PostgreSQL.

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/migrations/postgresql/001_initial.sql`

**SQL to add:**
```sql
CREATE TABLE public.projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    tenant_schema TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    max_memories INTEGER,
    max_sessions INTEGER,
    memory_ttl_default INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, slug)  -- FK added in Phase 4
);
```

**Dependencies:** Phase 1 (PostgreSQL backend exists)
**Verification:** Tables exist after migration runs.

#### Task 3.2 — Implement Tenant Resolver Middleware

**Objective:** Middleware that resolves `project_id` from API key → looks up `tenant_schema` → injects into `request.state`.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/middleware/tenant.py`

```python
class TenantResolverMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        auth = getattr(request.state, "auth", None)
        if auth and auth.get("project_id"):
            project_id = auth["project_id"]
            schema = await self._get_tenant_schema(project_id)
            request.state.tenant_schema = schema
            request.state.project_id = project_id
        return await call_next(request)
```

**Dependencies:** Task 3.1
**Verification:** Requests with a project-scoped API key have `request.state.tenant_schema` set.

#### Task 3.3 — Create Tenant Provisioning Job

**Objective:** Background job that creates schema + tables when a new project is created.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/jobs/__init__.py`
- `~/MemoryBridge/src/memory_bridge/jobs/tenant.py`

```python
async def provision_tenant_schema(ctx, project_id: str):
    """Creates schema and all tenant tables for a new project."""
    schema_name = f"tenant_{project_id.replace('-', '_')}"
    async with pool.acquire() as conn:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
        await conn.execute(f"""CREATE TABLE {schema_name}.sessions (...)
                               CREATE TABLE {schema_name}.memories (...)
                               -- indexes, FTS config""")
```

**Dependencies:** Task 3.1
**Verification:** Creating a project triggers schema creation. Tenant tables exist.

#### Task 3.4 — Update Auth Middleware for JWT + API Key

**Objective:** Support dual auth: API keys (machine-to-machine) and JWT tokens (user sessions).

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/middleware/auth.py` (rewrite)

Support both:
1. `Authorization: Bearer mb_sk_xxx...` → API key lookup → `request.state.auth = {key_info}`
2. `Authorization: Bearer eyJ...` → JWT decode → `request.state.auth = {user_info}`

**Dependencies:** Task 3.2
**Verification:** Both auth methods work. Invalid tokens return 401.

#### Task 3.5 — Add Project ID Inference from API Key

**Objective:** When a request uses an API key scoped to a project, auto-infer the `project_id` without the client specifying it.

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/services/auth_service.py` (create)

**Dependencies:** Task 3.4
**Verification:** Requests without explicit `project` parameter work if API key is project-scoped.

#### Task 3.6 — Update Controllers for Project Context

**Objective:** All controllers pass `project_id` and `tenant_schema` from `request.state` to services.

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/controllers/memory_controller.py`
- `~/MemoryBridge/src/memory_bridge/controllers/session_controller.py`
- `~/MemoryBridge/src/memory_bridge/controllers/handoff_controller.py`

**Dependencies:** Task 3.2
**Verification:** All API operations are scoped to the correct tenant schema.

#### Task 3.7 — RLS Fallback for Free Tier

**Objective:** For free/starter tiers, use shared schema with Row-Level Security instead of schema-per-tenant. This saves schema creation overhead for small tenants.

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/repository/postgres_repo.py`

**Dependencies:** Task 3.1
**Verification:** Free-tier tenants operate in shared schema with RLS enforcing isolation.

#### Task 3.8 — Multi-Tenant Integration Tests

**Objective:** End-to-end tests with multiple tenants verifying data isolation.

**Files to create:**
- `~/MemoryBridge/tests/integration/test_multi_tenant.py`

**Verification:** Tenant A's data is invisible to Tenant B. Cross-tenant queries return empty.

---

### Phase 4: User System & Billing

**Objective:** User registration, login, JWT sessions, Stripe subscriptions, usage metering, tier enforcement.

**Unlocks Nova Vision:** Free tier (100MB, 10K queries/day), Pro upgrades, the growth flywheel

**Estimated effort:** High (2 weeks)

#### Task 4.1 — Create `UserService`

**Objective:** User registration, login with bcrypt password hashing, JWT generation.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/services/user_service.py`

**Dependencies:** Phase 3 (users table exists)
**Verification:** Register → login → JWT → authenticated request flow works.

#### Task 4.2 — Create Auth Endpoints

**Objective:** `/auth/register`, `/auth/login`, `/auth/refresh` endpoints.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/controllers/auth_controller.py`

**Dependencies:** Task 4.1
**Verification:** Full register/login flow with JWT token exchange.

#### Task 4.3 — Create `BillingService`

**Objective:** Stripe subscription management, webhook processing.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/services/billing_service.py`

**Dependencies:** Task 4.1
**Verification:** Creating a Stripe subscription creates a local subscription record.

#### Task 4.4 — Create Stripe Webhook Handler

**Objective:** Handle Stripe events: `checkout.session.completed`, `invoice.paid`, `customer.subscription.updated`.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/controllers/billing_controller.py`

**Dependencies:** Task 4.3
**Verification:** Stripe test webhooks update subscription state in local DB.

#### Task 4.5 — Create `MeteringService`

**Objective:** Track per-organization usage metrics (memory writes, reads, storage bytes).

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/services/metering_service.py`

**Dependencies:** Task 4.1
**Verification:** Usage records are created and queryable.

#### Task 4.6 — Tier Limit Enforcement in Services

**Objective:** Services check tier limits before allowing operations.

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/services/memory_service.py`

**Dependencies:** Task 4.5
**Verification:** Exceeding free-tier limits returns 429 with upgrade prompt.

#### Task 4.7 — Create Usage Aggregation Job

**Objective:** Hourly aggregation of raw usage → Stripe metered billing.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/jobs/metering.py`

**Dependencies:** Task 4.5
**Verification:** Hourly aggregates are computed and stored.

#### Task 4.8 — Create Email Job

**Objective:** Send transactional emails (invitations, invoices, alerts).

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/jobs/email.py`

**Dependencies:** Task 4.1
**Verification:** Email job can be enqueued and processed.

---

### Phase 5: Admin & Observability

**Objective:** Admin API, Prometheus multi-process metrics, Grafana dashboards, structured logging.

**Fixes Rex Issues:** C10 (Prometheus single-process → multi-process with pushgateway or `PROMETHEUS_MULTIPROC_DIR`)

**Unlocks Nova Vision:** Dashboard, health panel, operational visibility

**Estimated effort:** Medium (2 weeks)

#### Task 5.1 — Create `AdminService`

**Objective:** Admin operations: user management, project management, analytics.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/services/admin_service.py`

**Dependencies:** Phase 4 (users exist)
**Verification:** Admin can list/suspend users, manage projects, view analytics.

#### Task 5.2 — Create Admin Controllers

**Objective:** Admin API endpoints with role-based access.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/controllers/admin_controller.py`

Endpoints:
- `GET /admin/users` (paginated)
- `GET /admin/orgs` (paginated)
- `GET /admin/projects` (paginated)
- `GET /admin/analytics/overview`
- `PATCH /admin/users/{user_id}` (suspend, role change)

**Dependencies:** Task 5.1
**Verification:** Admin can manage users via API. Non-admin gets 403.

#### Task 5.3 — Add Per-Endpoint Prometheus Metrics

**Objective:** Replace simple request counter with per-endpoint histograms and per-tenant gauges.

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/metrics.py`

**Dependencies:** Task 5.2
**Verification:** `/metrics` endpoint shows per-endpoint latency histograms.

#### Task 5.4 — Add Health Check Endpoints

**Objective:** Readiness, liveness, and dependency health endpoints.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/controllers/health_controller.py`

Endpoints:
- `GET /health` — liveness (always 200)
- `GET /health/ready` — readiness (checks DB, Redis, queue)
- `GET /health/db` — DB connection pool status
- `GET /health/redis` — Redis ping

**Dependencies:** Task 5.2
**Verification:** Kubernetes probes work correctly. Readiness fails when DB is down.

#### Task 5.5 — Add Structured Logging

**Objective:** Replace `print`/basic logging with structured JSON logs (request ID, tenant, latency).

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/middleware/logging.py`

**Dependencies:** Phase 3 (tenant context exists)
**Verification:** Logs are JSON-formatted with `request_id`, `tenant_schema`, `latency_ms`.

#### Task 5.6 — Create Grafana Dashboards

**Objective:** Operational dashboards for memory operations, latency, error rates.

**Files to create:**
- `~/MemoryBridge/docs/grafana/dashboards/memory-bridge-overview.json`
- `~/MemoryBridge/docs/grafana/dashboards/memory-bridge-tenants.json`

**Dependencies:** Task 5.3
**Verification:** Dashboards can be imported into Grafana and display live data.

---

### Phase 6: Scale to Production

**Objective:** Read replicas, connection pooling (PgBouncer), load testing, production runbook.

**Unlocks Nova Vision:** 99.9% SLA, production deployments

**Estimated effort:** High (2 weeks)

#### Task 6.1 — Add PgBouncer Connection Pooling

**Objective:** Add PgBouncer sidecar to manage PostgreSQL connections efficiently.

**Files to modify:**
- `~/MemoryBridge/docker-compose.yml`

**Dependencies:** Phase 1 (PostgreSQL backend)
**Verification:** Connections go through PgBouncer.

#### Task 6.2 — Separate Read/Write Pool

**Objective:** Repository uses separate connection pools for reads (replicas) and writes (primary).

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/repository/postgres_repo.py`

**Dependencies:** Task 6.1
**Verification:** Writes go to primary. Reads go to replica (configurable).

#### Task 6.3 — Materialized Views for Analytics

**Objective:** Pre-computed aggregates for dashboard performance.

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/migrations/postgresql/`

**Dependencies:** Phase 5 (analytics)
**Verification:** Session summaries and hourly usage are queryable.

#### Task 6.4 — Load Testing

**Objective:** k6/locust scripts to validate performance under load.

**Files to create:**
- `~/MemoryBridge/tests/load/locustfile.py`

**Dependencies:** All prior phases
**Verification:** System handles 1000 concurrent users with p95 < 50ms.

#### Task 6.5 — Connection Leak Detection + Graceful Drain

**Objective:** Monitor pool stats. Drain connections gracefully on shutdown.

**Files to modify:**
- `~/MemoryBridge/src/memory_bridge/dependencies.py`

**Dependencies:** Task 6.1
**Verification:** No connection leaks after a load test.

#### Task 6.6 — Migration CI Pipeline

**Objective:** Database migrations run automatically on deploy.

**Files to create:**
- `~/MemoryBridge/.github/workflows/migrations.yml`

**Dependencies:** Task 1.7 (migration runner)
**Verification:** PR with migration changes runs migrations in CI.

#### Task 6.7 — Production Dockerfile

**Objective:** Multi-stage Dockerfile with HEALTHCHECK and production settings.

**Files to modify:**
- `~/MemoryBridge/Dockerfile`

**Dependencies:** Phase 1
**Verification:** Container builds and passes health checks.

#### Task 6.8 — Deployment Runbook

**Objective:** Document production deployment process.

**Files to create:**
- `~/MemoryBridge/docs/deployment.md`

**Dependencies:** All prior phases
**Verification:** A new engineer can deploy to production following the runbook.

---

### Phase 7: Hypergrowth

**Objective:** Sharding, event-driven architecture, CDN caching, S3 offloading for large values.

**Unlocks Nova Vision:** Enterprise scale, Nova's flywheel, millions of tenants

**Estimated effort:** Medium (4 weeks)

#### Task 7.1 — Implement `ShardRouter`

**Objective:** Route project requests to the correct database shard.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/repository/shard_router.py`

**Dependencies:** Phase 6
**Verification:** Projects are correctly routed to their shard.

#### Task 7.2 — Add Consistent Hash Ring

**Objective:** Deterministic project → shard mapping with minimal rebalancing on shard add/remove.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/repository/hash_ring.py`

**Dependencies:** Task 7.1
**Verification:** Adding a shard moves < 10% of projects.

#### Task 7.3 — Event Bus Abstraction

**Objective:** Pub/sub for memory events (writes, deletes, handoffs).

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/events/__init__.py`
- `~/MemoryBridge/src/memory_bridge/events/event_bus.py`

**Dependencies:** Phase 2 (Redis exists)
**Verification:** Memory write events are published and can be consumed.

#### Task 7.4 — Webhook Forwarding

**Objective:** User-configurable webhooks for memory events.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/events/webhook.py`

**Dependencies:** Task 7.3
**Verification:** Webhooks are delivered for configured project events.

#### Task 7.5 — S3 Offloading for Large Values

**Objective:** Store memory values > 64KB in S3/MinIO instead of the database.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/repository/s3_store.py`

**Dependencies:** Phase 1
**Verification:** Large values are stored in S3, referenced by key in DB.

#### Task 7.6 — CDN Caching Configuration

**Objective:** Cache headers for appropriate endpoints.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/middleware/cache_headers.py`

**Dependencies:** Phase 2
**Verification:** Responses include appropriate `Cache-Control` headers.

#### Task 7.7 — Re-sharding Tooling

**Objective:** Scripts to rebalance shards during maintenance windows.

**Files to create:**
- `~/MemoryBridge/src/memory_bridge/jobs/rebalance.py`

**Dependencies:** Tasks 7.1, 7.2
**Verification:** Re-sharding moves data correctly without data loss.

#### Task 7.8 — Chaos Engineering Tests

**Objective:** Verify system resilience under failure scenarios.

**Files to create:**
- `~/MemoryBridge/tests/chaos/test_network_failure.py`
- `~/MemoryBridge/tests/chaos/test_db_failover.py`

**Dependencies:** Phase 6
**Verification:** System survives DB failover, network partition, process crash.

---

## Section 3: Gate Criteria

### Phase 0 → Phase 1 Gate

- [ ] All existing tests pass (`pytest tests/ -v` → green)
- [ ] Open mode bypass is closed (C1): server without `MEMORY_BRIDGE_API_KEY` rejects requests with 401
- [ ] API key hashing uses bcrypt, not SHA-256 (C2 partial)
- [ ] Request size limits enforced: >10MB body returns 413 (C5)
- [ ] Metrics endpoint requires auth by default (C9)
- [ ] Git tag: `v0.2.1-security`

### Phase 1 → Phase 2 Gate

- [ ] All existing tests pass
- [ ] `MemoryRepository` ABC defined in `repository/__init__.py`
- [ ] `SQLiteMemoryRepository` passes all contract tests
- [ ] `PostgresMemoryRepository` passes all contract tests
- [ ] Dual-backend migration system works (SQLite + PostgreSQL)
- [ ] `Settings` class reads all config from env vars
- [ ] DI factory manages pool lifecycle correctly
- [ ] `docker compose up` starts PostgreSQL + Redis + app
- [ ] Git tag: `v0.3.0-storage-refactor`

### Phase 2 → Phase 3 Gate

- [ ] All existing tests pass
- [ ] `MemoryService`, `SessionService`, `HandoffService` exist and are tested
- [ ] `CacheService` works with Redis
- [ ] Controllers are split into `controllers/` package
- [ ] Rate limiter is Redis-backed (survives restarts, shared across workers)
- [ ] In-memory `ratelimit.py` is deprecated/removed (C8 fixed)
- [ ] API key hashing uses bcrypt properly (C2 fully fixed)
- [ ] Git tag: `v0.4.0-service-layer`

### Phase 3 → Phase 4 Gate

- [ ] All existing tests pass
- [ ] Public schema tables exist (projects, api_keys)
- [ ] Tenant resolver middleware resolves `project_id` → `tenant_schema`
- [ ] Tenant provisioning job creates schema + tables on project creation
- [ ] Auth middleware supports both JWT and API keys
- [ ] Project ID is auto-inferred from project-scoped API keys
- [ ] RLS fallback works for free-tier tenants
- [ ] Multi-tenant integration tests pass (data isolation confirmed)
- [ ] C3 and C4 are marked as fixed in the security register
- [ ] Git tag: `v0.5.0-multi-tenant`

### Phase 4 → Phase 5 Gate

- [ ] All existing tests pass
- [ ] User registration + login + JWT flow works end-to-end
- [ ] Stripe webhooks create/update/cancel subscriptions
- [ ] Usage metering records all API operations
- [ ] Tier limits are enforced (free tier: 100MB, 10K queries/day)
- [ ] Exceeding limits returns 429 with upgrade link
- [ ] Hourly usage aggregation job runs on schedule
- [ ] Email sending works for invitations
- [ ] Git tag: `v0.6.0-user-billing`

### Phase 5 → Phase 6 Gate

- [ ] All existing tests pass
- [ ] Admin API is functional (user management, project management, analytics)
- [ ] Health endpoints respond correctly (liveness, readiness, deps)
- [ ] Prometheus exposes multi-process metrics (C10 fixed)
- [ ] Logs are structured JSON with request_id, tenant, latency
- [ ] Grafana dashboards can be imported and display data
- [ ] Git tag: `v0.7.0-admin-observability`

### Phase 6 → Phase 7 Gate

- [ ] All existing tests pass
- [ ] PgBouncer connection pooling active
- [ ] Read/write pool separation works
- [ ] Load test: 1000 concurrent users, p95 < 50ms memory write
- [ ] No connection leaks after 1-hour load test
- [ ] Migration CI runs on every deploy
- [ ] Dockerfile builds and passes health checks
- [ ] Deployment runbook is documented and verified
- [ ] Git tag: `v1.0.0-production`

### Phase 7 Gate (Post-Launch)

- [ ] All existing tests pass
- [ ] ShardRouter maps projects to correct shards
- [ ] Consistent hash ring rebalances correctly on shard add/remove
- [ ] Event bus publishes memory events
- [ ] Webhooks deliver events to user-configured endpoints
- [ ] S3 stores large memory values (>64KB)
- [ ] CDN cache headers are correct
- [ ] Chaos tests pass: DB failover, network partition
- [ ] Git tag: `v1.1.0-hypergrowth`

---

## Section 4: Risk Register

### Phase 0 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **R0.1** Open mode fix breaks self-hosted users who rely on it | Medium | High | Add `MEMORY_BRIDGE_ALLOW_OPEN=true` env var. Log warning. Document in migration guide. |
| **R0.2** bcrypt change invalidates existing API keys | Low | High | On upgrade, re-hash existing SHA-256 hashes with bcrypt on first authenticate call. Use hash format detection (starts with `$2b$`). |
| **R0.3** Request size limit blocks legitimate large payloads | Low | Medium | Default to 10MB. Make configurable via env var. Document in API reference. |

### Phase 1 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **R1.1** SQLite repo extraction breaks existing API contract | Medium | High | Run full test suite before and after. Use contract tests (same tests against both backends). |
| **R1.2** PostgreSQL migration incompatible with existing SQLite data | Low | High | Migration runner handles differences. SQLite users stay on SQLite. No automatic data migration. |
| **R1.3** asyncpg pool leaks connections under load | Medium | Medium | Add pool metrics, connection leak detection (Phase 6). Test pool behavior early. |
| **R1.4** Dual-backend maintenance burden doubles test surface | Medium | Low | Contract tests reduce duplication. SQLite can use a subset of tests for smoke testing. |

### Phase 2 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **R2.1** Service layer adds latency without caching | Low | Medium | CacheService added in same phase. Hot paths cache-first. |
| **R2.2** Redis rate limiter becomes a bottleneck | Low | Medium | Single Redis `INCR` + `EXPIRE` is O(1) and fast. Monitor Redis latency. |
| **R2.3** Controller refactor breaks API consumers | Medium | High | Keep old `main.py` routes as redirects. Use FastAPI's `include_router` with same paths. |

### Phase 3 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **R3.1** Schema-per-tenant creates too many schemas | Low | Medium | Free/starter tiers use RLS (shared schema). Only Pro+ gets schema-per-tenant. |
| **R3.2** Tenant provisioning is slow (sync) | Medium | Medium | Make it async (enqueue job). Return 202 Accepted. Client polls for readiness. |
| **R3.3** JWT + API key dual-auth is confusing | Medium | Low | Document clearly: "API keys for machines, JWT for humans." Auto-detect by token prefix. |

### Phase 4 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **R4.1** Stripe webhook delivery failure leads to billing inconsistency | Low | High | Idempotent webhook processing with retry. Manual reconciliation tool. |
| **R4.2** JWT secret rotation causes all users to re-login | Low | Medium | Support multiple valid secrets during rotation window. |
| **R4.3** Free tier abuse (one person, many accounts) | Medium | Medium | Rate limit by IP on registration. Email verification. |

### Phase 5 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **R5.1** Prometheus multi-process mode is finicky | Medium | Medium | Use `PROMETHEUS_MULTIPROC_DIR`. Test with Gunicorn before production. |
| **R5.2** Admin API becomes an attack surface | Medium | High | Admin endpoints require `role=admin`. Audit-log every admin action. |

### Phase 6 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **R6.1** PgBouncer adds latency | Low | Medium | Use session pooling (not transaction pooling) for prepared statements. PgBouncer overhead is < 1ms. |
| **R6.2** Read replicas serve stale data | Low | Medium | Use `REPEATABLE READ` for sensitive reads. Replication lag monitoring with alerts. |

### Phase 7 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **R7.1** Sharding leads to cross-shard query complexity | Medium | High | Avoid cross-shard queries. If needed, use scatter-gather pattern. Shard by project_id (all data for a project is on one shard). |
| **R7.2** Re-sharding is risky and downtime-prone | Medium | High | Use virtual shards (256 logical → 8-32 physical). Add shards during low-traffic windows. Automated rebalance with verification. |

---

## Section 5: Quick Wins (Day 1)

These are fixes that take **<30 minutes each** and can be done immediately while planning the bigger phases.

| # | Fix | File | Time | Rex Issue |
|---|-----|------|------|-----------|
| ✅ **QW1** | Close open mode bypass — make auth required by default | `auth.py` | 15 min | C1 |
| ✅ **QW2** | Add request body size limit middleware (10MB default) | `main.py` | 15 min | C5 |
| ✅ **QW3** | Protect `/metrics` endpoint behind auth by default | `main.py`, `auth.py` | 10 min | C9 |
| ✅ **QW4** | Add `MEMORY_BRIDGE_ALLOW_OPEN` env var documentation | `.env.example` | 5 min | C1 |
| ✅ **QW5** | Log warning when server starts with no API key configured | `main.py` | 5 min | C1 |
| ✅ **QW6** | Add rate limit headers (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `Retry-After`) to 429 responses | `main.py` (middleware) | 10 min | — |
| ✅ **QW7** | Add `Content-Security-Policy` header | `main.py` (middleware) | 10 min | — |
| ✅ **QW8** | Pin dependency versions in `pyproject.toml` to prevent supply-chain issues | `pyproject.toml` | 5 min | — |
| ✅ **QW9** | Add `.env.example` with all config vars documented | Root | 15 min | — |
| ✅ **QW10** | Add health endpoint to Prometheus scrape exemption for auth middleware | `auth.py` | 2 min | — |

### Quick Win Implementation Order

```mermaid
graph LR
    A[QW1: Fix open mode] --> B[QW3: Protect /metrics]
    B --> C[QW2: Body size limits]
    C --> D[QW5: Warning on no-key startup]
    D --> E[QW4: ALLOW_OPEN env var]
    E --> F[QW6-10: Polish]
```

---

## Appendix A: Complete File Change Manifest

### Files to Create (40 new files)

```
src/memory_bridge/
├── config.py                          # Phase 1 — Pydantic Settings
├── repository/
│   ├── __init__.py                    # Phase 1 — MemoryRepository ABC
│   ├── base.py                        # Phase 1 — Common utilities
│   ├── sqlite_repo.py                 # Phase 1 — SQLite backend
│   ├── postgres_repo.py               # Phase 1 — PostgreSQL backend
│   ├── shard_router.py                # Phase 7 — Shard routing
│   ├── hash_ring.py                   # Phase 7 — Consistent hashing
│   └── s3_store.py                    # Phase 7 — Large value offloading
├── services/
│   ├── __init__.py                    # Phase 2
│   ├── memory_service.py              # Phase 2
│   ├── session_service.py             # Phase 2
│   ├── handoff_service.py             # Phase 2
│   ├── auth_service.py                # Phase 3
│   ├── cache_service.py               # Phase 2
│   ├── metering_service.py            # Phase 4
│   ├── billing_service.py             # Phase 4
│   ├── admin_service.py               # Phase 5
│   └── user_service.py                # Phase 4
├── controllers/
│   ├── __init__.py                    # Phase 2
│   ├── memory_controller.py           # Phase 2
│   ├── session_controller.py          # Phase 2
│   ├── handoff_controller.py          # Phase 2
│   ├── auth_controller.py             # Phase 4
│   ├── billing_controller.py          # Phase 4
│   ├── admin_controller.py            # Phase 5
│   └── health_controller.py           # Phase 5
├── middleware/
│   ├── __init__.py                    # Phase 2
│   ├── rate_limit.py                  # Phase 2
│   ├── tenant.py                      # Phase 3
│   ├── logging.py                     # Phase 5
│   └── cache_headers.py               # Phase 7
├── models/
│   ├── __init__.py                    # Phase 1 — Split from models.py
│   ├── memory.py                      # Phase 1
│   ├── session.py                     # Phase 1
│   ├── handoff.py                     # Phase 1
│   ├── user.py                        # Phase 4
│   ├── billing.py                     # Phase 4
│   └── api_key.py                     # Phase 3
├── jobs/
│   ├── __init__.py                    # Phase 3
│   ├── cleanup.py                     # Phase 3
│   ├── metering.py                    # Phase 4
│   ├── email.py                       # Phase 4
│   ├── tenant.py                      # Phase 3
│   ├── stripe_webhooks.py             # Phase 4
│   ├── audit.py                       # Phase 5
│   └── rebalance.py                   # Phase 7
├── events/
│   ├── __init__.py                    # Phase 7
│   ├── event_bus.py                   # Phase 7
│   └── webhook.py                     # Phase 7
└── migrations/
    ├── __init__.py                    # Phase 1
    ├── base.py                        # Phase 1
    ├── runner.py                      # Phase 1
    ├── schema_version.py              # Phase 1
    ├── sqlite/                        # Phase 1
    │   ├── 001_initial.sql
    │   └── 002_add_handoffs.sql
    └── postgresql/                    # Phase 1
        ├── 001_initial.sql
        └── 002_add_handoffs.sql

docker-compose.yml                     # Phase 1

tests/
├── integration/
│   ├── conftest.py                    # Phase 1
│   ├── test_memory_repo.py            # Phase 1
│   ├── test_session_repo.py           # Phase 1
│   ├── test_api_endpoints.py          # Phase 2
│   ├── test_multi_tenant.py           # Phase 3
│   └── test_jobs.py                   # Phase 4
├── unit/
│   ├── test_memory_service.py         # Phase 2
│   ├── test_session_service.py        # Phase 2
│   ├── test_handoff_service.py        # Phase 2
│   ├── test_auth_service.py           # Phase 3
│   ├── test_metering_service.py       # Phase 4
│   ├── test_billing_service.py        # Phase 4
│   ├── test_cache_service.py          # Phase 2
│   └── test_admin_service.py          # Phase 5
├── e2e/
│   ├── test_full_flow.py              # Phase 4
│   └── test_multi_tenant.py           # Phase 3
├── load/
│   └── locustfile.py                  # Phase 6
└── chaos/                             # Phase 7
    ├── test_network_failure.py
    └── test_db_failover.py

docs/
├── deployment.md                      # Phase 6
├── api-reference.md                   # Phase 4
├── development.md                     # Phase 1
├── grafana/dashboards/
│   ├── memory-bridge-overview.json    # Phase 5
│   └── memory-bridge-tenants.json     # Phase 5
└── prometheus/
    └── alerts.yml                     # Phase 5

.github/workflows/
└── migrations.yml                     # Phase 6
```

### Files to Modify (10 existing files)

| File | Phase | Change |
|------|-------|--------|
| `src/memory_bridge/main.py` | Phase 2 | Rewrite as app factory, import controllers |
| `src/memory_bridge/dependencies.py` | Phase 1 | Rewrite with DI factory + pool lifecycle |
| `src/memory_bridge/models.py` | Phase 1 | Split into `models/` package (keep import shim) |
| `src/memory_bridge/auth.py` | Phase 0, 3 | Fix open mode (P0), then JWT + API key dual-auth (P3) |
| `src/memory_bridge/storage.py` | Phase 1 | Extract → `SQLiteMemoryRepository`, keep as wrapper |
| `src/memory_bridge/ratelimit.py` | Phase 2 | Deprecate/remove (replaced by Redis-backed) |
| `src/memory_bridge/metrics.py` | Phase 5 | Add per-endpoint histograms |
| `src/memory_bridge/cli.py` | Phase 4 | Add admin CLI commands |
| `pyproject.toml` | Phase 1 | Add asyncpg, pydantic-settings, bcrypt, redis, arq, stripe |
| `Dockerfile` | Phase 6 | Multi-stage with libpq |

### Files to Remove (3 existing files)

| File | Phase | Replacement |
|------|-------|-------------|
| `src/memory_bridge/auth.py` | Phase 3 | Split into `middleware/auth.py` + `services/auth_service.py` |
| `src/memory_bridge/storage.py` | Phase 1 | Split into `repository/sqlite_repo.py` |
| `src/memory_bridge/ratelimit.py` | Phase 2 | Replaced by `middleware/rate_limit.py` |

---

## Appendix B: Key Metrics Dashboard

| Metric | Current (v0.2) | Target (v1.0) | Measured By |
|--------|---------------|---------------|-------------|
| Test status | All passing | All passing + contract tests | `pytest tests/` |
| Open mode bypass | ✅ Open by default | ❌ Closed by default | Integration test |
| Rate limiter | In-memory (lost on restart) | Redis-backed (survives restart) | Redis key persistence |
| Auth hashing | SHA-256 | bcrypt | Hash format check |
| Request size limit | None | 10MB configurable | Integration test |
| Storage backend | SQLite only | SQLite + PostgreSQL | Contract tests |
| Data isolation | None | Schema-per-tenant + RLS | Multi-tenant tests |
| Multi-process metrics | ❌ Single process | ✅ Multi-process | Grafana dashboard |
| Max tenants | 1 | Unlimited | Load test |
| P50 write latency | Unknown | < 10ms | Prometheus histogram |
| P95 write latency | Unknown | < 50ms | Prometheus histogram |
| SaaS readiness | ❌ | ✅ | Feature-complete checklist |

---

## Summary: What We're Building

```
PHASE 0 (Days 1-2):   Security hotfixes → patched vulnerabilities
PHASE 1 (Week 1-2):   Storage refactor → SQLite AND PostgreSQL work
PHASE 2 (Week 3-4):   Service layer → business logic separated, Redis rate limiting
PHASE 3 (Week 5-6):   Multi-tenancy → project isolation for SaaS
PHASE 4 (Week 7-8):   Users + billing → Stripe subscriptions, free tier
PHASE 5 (Week 9-10):  Admin + observability → dashboards, monitoring
PHASE 6 (Week 11-12): Scale → PgBouncer, replicas, load testing
PHASE 7 (Week 13-16): Hypergrowth → sharding, events, CDN

MILESTONE: v1.0 SaaS Launch at end of Phase 6 (Week 12)
```

**Start with the Quick Wins (Section 5). Then hit Phase 1. Each phase ships independently. Move fast, break nothing. 🚀**
