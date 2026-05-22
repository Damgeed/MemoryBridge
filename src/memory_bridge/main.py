import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from .dependencies import get_storage
from .handoff import HandoffProtocol
from .metrics import (
    request_counter,
    memory_gauge,
    session_gauge,
    uptime_gauge,
    request_latency,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from .models import MemoryEntry, MemoryCreate, MemoryQuery, Session, HandoffPayload
from .storage import MemoryStorage
from .auth import APIKeyMiddleware
from .ratelimit import RateLimiter

logger = logging.getLogger(__name__)

# How often the background cleanup runs (seconds). Default: 5 minutes.
_CLEANUP_INTERVAL = int(os.environ.get("MEMORY_BRIDGE_CLEANUP_INTERVAL", "300"))
# Default TTL for new memories (seconds). 0 or unset = no default TTL.
_DEFAULT_TTL = int(os.environ.get("MEMORY_BRIDGE_DEFAULT_TTL", "0")) or None
# Rate limit (requests per minute per IP). Default: 60.
_RATE_LIMIT = int(os.environ.get("MEMORY_BRIDGE_RATE_LIMIT", "60"))
# CORS origins (comma-separated). Default: allow all.
_CORS_ORIGINS = os.environ.get("MEMORY_BRIDGE_CORS_ORIGINS", "*").split(",")

_limiter = RateLimiter(requests_per_minute=_RATE_LIMIT)


async def _cleanup_loop(storage: MemoryStorage):
    """Periodically delete expired memories."""
    while True:
        try:
            await asyncio.sleep(_CLEANUP_INTERVAL)
            # Warn if cleanup hasn't run in > 2x the interval
            last_cleanup = await storage.get_metric("last_cleanup_at")
            if last_cleanup is not None:
                last_cleanup_dt = datetime.fromisoformat(last_cleanup)
                seconds_since = (datetime.now(timezone.utc) - last_cleanup_dt).total_seconds()
                if seconds_since > 2 * _CLEANUP_INTERVAL:
                    logger.warning(
                        "Cleanup hasn't run in %.0f seconds (interval=%ds)",
                        seconds_since, _CLEANUP_INTERVAL,
                    )
            deleted = await storage.cleanup_expired()
            if deleted:
                logger.info("Cleanup: deleted %d expired memories", deleted)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Cleanup task error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize storage and background cleanup on startup."""
    storage = await get_storage()
    await storage.initialize()

    # Initialize shared metrics (set-once, safe for multi-worker)
    await storage.initialize_metric("start_time", datetime.now(timezone.utc).isoformat())
    await storage.initialize_metric("request_count", 0)
    await storage.initialize_metric("total_latency_ms", 0.0)

    # Set up Prometheus uptime gauge (auto-updates on scrape)
    uptime_gauge.set_function(lambda: (datetime.now(timezone.utc) - _start_time).total_seconds())

    # Start background cleanup task
    cleanup_task = asyncio.create_task(_cleanup_loop(storage))

    yield

    # Shutdown: cancel cleanup task
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Memory Bridge",
    version="0.2.0",
    description="Cross-session memory persistence for multi-agent teams",
    lifespan=lifespan,
)

# Module-level start time for Prometheus uptime gauge
_start_time = datetime.now(timezone.utc)

# ── Middleware Stack (order matters) ──────────────────────────────────────

# 1. CORS — handle preflight and allow cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Auth — Bearer token check on non-/health routes
app.add_middleware(APIKeyMiddleware)


# 3. Core middleware — rate limiting, request ID, metrics recording
@app.middleware("http")
async def core_middleware(request: Request, call_next):
    """Consolidated middleware: rate limit → request ID → record metrics."""

    # Rate limit check (skip for health and metrics endpoints)
    if request.url.path not in ("/health", "/metrics"):
        client_ip = request.client.host if request.client else "unknown"
        allowed = await _limiter.check(client_ip)
        if not allowed:
            return Response(
                content='{"detail":"Rate limit exceeded. Try again in 60 seconds."}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": "60", "X-Request-ID": ""},
            )

    # Generate request ID
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id

    # Process request
    start = time.monotonic()
    response = await call_next(request)
    elapsed_ms = (time.monotonic() - start) * 1000

    # Add request ID header
    response.headers["X-Request-ID"] = request_id

    # Record metrics (fire-and-forget, don't block the response)
    try:
        storage = await get_storage()
        await storage.increment_metric("request_count")
        await storage.increment_metric("total_latency_ms", round(elapsed_ms, 1))
    except Exception:
        logger.exception("Failed to record storage metrics")

    # Record Prometheus metrics
    request_counter.inc()
    request_latency.observe(elapsed_ms / 1000.0)

    return response


# ── Health ────────────────────────────────────────────────────────────────


@app.get("/health")
async def health(storage: MemoryStorage = Depends(get_storage)):
    metrics = await storage.get_all_metrics()

    start_time_str = metrics.get("start_time")
    if start_time_str:
        start_dt = datetime.fromisoformat(start_time_str)
        uptime = int((datetime.now(timezone.utc) - start_dt).total_seconds())
    else:
        uptime = 0

    req_count = metrics.get("request_count", 0)
    total_lat = metrics.get("total_latency_ms", 0.0)
    avg_latency = (total_lat / req_count) if req_count > 0 else 0.0

    last_cleanup = metrics.get("last_cleanup_at")
    if last_cleanup:
        last_cleanup_dt = datetime.fromisoformat(last_cleanup)
        last_cleanup_seconds_ago = int((datetime.now(timezone.utc) - last_cleanup_dt).total_seconds())
    else:
        last_cleanup_seconds_ago = None

    sessions_total = await storage.count_sessions()
    memories_total = await storage.count_memories()

    return {
        "status": "ok",
        "service": "memory-bridge",
        "version": "0.2.0",
        "uptime_seconds": uptime,
        "sessions_total": sessions_total,
        "memories_total": memories_total,
        "avg_latency_ms": round(avg_latency, 3),
        "requests_served": req_count,
        "last_cleanup_seconds_ago": last_cleanup_seconds_ago,
    }


# ── Prometheus Metrics ────────────────────────────────────────────────────


@app.get("/metrics")
async def metrics(storage: MemoryStorage = Depends(get_storage)):
    """Expose Prometheus metrics at /metrics.

    Updates memory and session gauges from storage, then returns the
    latest Prometheus exposition format.
    """
    memory_gauge.set(await storage.count_memories())
    session_gauge.set(await storage.count_sessions())

    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


# --- Memory CRUD ---


@app.post("/memories", response_model=MemoryEntry)
async def create_memory(
    payload: MemoryCreate,
    request: Request,
    storage: MemoryStorage = Depends(get_storage),
):
    # Apply default TTL if set and no TTL was specified
    ttl = payload.ttl_seconds if payload.ttl_seconds is not None else _DEFAULT_TTL
    # Inherit project from auth if not explicitly set in payload
    project = payload.project
    if project is None and hasattr(request.state, "auth") and request.state.auth:
        project = request.state.auth.get("project_id")
    entry = MemoryEntry(
        session_id=payload.session_id,
        agent_id=payload.agent_id,
        key=payload.key,
        value=payload.value,
        tags=payload.tags,
        ttl_seconds=ttl,
        project=project,
    )
    return await storage.store_memory(entry, propagate_to_parent=payload.propagate_to_parent)


@app.get("/memories/search")
async def search_memories(
    request: Request,
    q: str = Query(..., description="Full-text search query"),
    session_id: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    storage: MemoryStorage = Depends(get_storage),
):
    # Inherit project from auth if available
    project = None
    if hasattr(request.state, "auth") and request.state.auth:
        project = request.state.auth.get("project_id")
    entries = await storage.search_memories(
        query=q, limit=limit, offset=offset,
        session_id=session_id, agent_id=agent_id,
        project=project,
    )
    return {"entries": [e.model_dump() for e in entries], "total": len(entries)}


@app.get("/memories/{memory_id}", response_model=MemoryEntry)
async def get_memory(
    memory_id: str,
    storage: MemoryStorage = Depends(get_storage),
):
    entry = await storage.get_memory(memory_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return entry


@app.post("/memories/query")
async def query_memories(
    request: Request,
    query: MemoryQuery,
    storage: MemoryStorage = Depends(get_storage),
    include_lineage: bool = Query(False, description="If True, also query parent sessions in the lineage"),
):
    # Inherit project from auth if not explicitly set in query
    project = query.project
    if project is None and hasattr(request.state, "auth") and request.state.auth:
        project = request.state.auth.get("project_id")
    if include_lineage and query.session_id:
        entries = await storage.query_memories_lineage(
            session_id=query.session_id,
            agent_id=query.agent_id,
            tags=query.tags,
            keys=query.keys,
            limit=query.limit,
            offset=query.offset,
            project=project,
        )
    else:
        entries = await storage.query_memories(
            session_id=query.session_id,
            agent_id=query.agent_id,
            tags=query.tags,
            keys=query.keys,
            limit=query.limit,
            offset=query.offset,
            project=project,
        )
    return {"entries": [e.model_dump() for e in entries], "total": len(entries)}


@app.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: str,
    storage: MemoryStorage = Depends(get_storage),
):
    deleted = await storage.delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}


# --- Session CRUD ---


@app.post("/sessions", response_model=Session)
async def create_session(
    session: Session,
    storage: MemoryStorage = Depends(get_storage),
):
    return await storage.store_session(session)


@app.get("/sessions/{session_id}", response_model=Session)
async def get_session(
    session_id: str,
    storage: MemoryStorage = Depends(get_storage),
):
    session = await storage.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


# --- Handoff Protocol ---


@app.post("/handoff/prepare")
async def prepare_handoff(
    payload: HandoffPayload,
    storage: MemoryStorage = Depends(get_storage),
):
    protocol = HandoffProtocol(storage)
    result = await protocol.prepare_handoff(
        from_agent_id=payload.from_agent_id,
        to_agent_id=payload.to_agent_id,
        session_id=payload.session_id,
        handoff_type=payload.handoff_type,
        include_tags=payload.include_tags,
    )
    return {
        "success": result.success,
        "summary": result.summary,
        "context": result.context,
        "warnings": result.warnings,
    }


@app.post("/handoff/execute")
async def execute_handoff(
    payload: HandoffPayload,
    storage: MemoryStorage = Depends(get_storage),
):
    protocol = HandoffProtocol(storage)
    result = await protocol.execute_handoff(
        from_agent_id=payload.from_agent_id,
        to_agent_id=payload.to_agent_id,
        session_id=payload.session_id,
        handoff_type=payload.handoff_type,
        include_tags=payload.include_tags,
    )
    return {
        "success": result.success,
        "summary": result.summary,
        "context": result.context,
        "warnings": result.warnings,
    }


# --- Admin: API Key Management ---


@app.post("/admin/keys")
async def admin_create_api_key(
    label: str = Query(..., description="Human-readable label for the key"),
    project_id: Optional[str] = Query(None, description="Optional project scope"),
    storage: MemoryStorage = Depends(get_storage),
):
    """Create a new API key. The full key is returned only once."""
    return await storage.create_api_key(label=label, project_id=project_id)


@app.get("/admin/keys")
async def admin_list_api_keys(
    storage: MemoryStorage = Depends(get_storage),
):
    """List all API keys (key hashes only, not the actual keys)."""
    keys = await storage.list_api_keys()
    return {"keys": keys}


@app.delete("/admin/keys/{key_id}")
async def admin_revoke_api_key(
    key_id: str,
    storage: MemoryStorage = Depends(get_storage),
):
    """Revoke an API key. It can no longer be used for authentication."""
    revoked = await storage.revoke_api_key(key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"revoked": True, "key_id": key_id}
