import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, HTTPException, Request, Query

from .dependencies import get_storage
from .handoff import HandoffProtocol
from .models import MemoryEntry, MemoryCreate, MemoryQuery, Session, HandoffPayload
from .storage import MemoryStorage, last_cleanup_at
from .auth import APIKeyMiddleware

logger = logging.getLogger(__name__)

# Operational metrics — module-level
START_TIME = datetime.now(timezone.utc)
request_count = 0
total_latency = 0.0

# How often the background cleanup runs (seconds). Default: 5 minutes.
_CLEANUP_INTERVAL = int(os.environ.get("MEMORY_BRIDGE_CLEANUP_INTERVAL", "300"))
# Default TTL for new memories (seconds). 0 or unset = no default TTL.
_DEFAULT_TTL = int(os.environ.get("MEMORY_BRIDGE_DEFAULT_TTL", "0")) or None


async def _cleanup_loop(storage: MemoryStorage):
    """Periodically delete expired memories."""
    while True:
        try:
            await asyncio.sleep(_CLEANUP_INTERVAL)
            # Warn if cleanup hasn't run in > 2x the interval
            if last_cleanup_at is not None:
                seconds_since = (datetime.now(timezone.utc) - last_cleanup_at).total_seconds()
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

# Auth middleware — checks Bearer token on all routes except /health
# Disabled when MEMORY_BRIDGE_API_KEY env var is not set
app.add_middleware(APIKeyMiddleware)


@app.middleware("http")
async def track_requests(request: Request, call_next):
    """Track request count and latency for operational metrics."""
    global request_count, total_latency
    start = time.monotonic()
    response = await call_next(request)
    elapsed = time.monotonic() - start
    request_count += 1
    total_latency += elapsed
    return response


@app.get("/health")
async def health(storage: MemoryStorage = Depends(get_storage)):
    uptime = int((datetime.now(timezone.utc) - START_TIME).total_seconds())
    sessions_total = await storage.count_sessions()
    memories_total = await storage.count_memories()
    avg_latency_ms = (total_latency / request_count * 1000) if request_count > 0 else 0.0
    # Calculate cleanup monitoring
    if last_cleanup_at is not None:
        last_cleanup_seconds_ago = int((datetime.now(timezone.utc) - last_cleanup_at).total_seconds())
    else:
        last_cleanup_seconds_ago = None
    return {
        "status": "ok",
        "service": "memory-bridge",
        "version": "0.2.0",
        "uptime_seconds": uptime,
        "sessions_total": sessions_total,
        "memories_total": memories_total,
        "avg_latency_ms": round(avg_latency_ms, 3),
        "requests_served": request_count,
        "last_cleanup_seconds_ago": last_cleanup_seconds_ago,
    }


# --- Memory CRUD ---


@app.post("/memories", response_model=MemoryEntry)
async def create_memory(
    payload: MemoryCreate,
    storage: MemoryStorage = Depends(get_storage),
):
    # Apply default TTL if set and no TTL was specified
    ttl = payload.ttl_seconds if payload.ttl_seconds is not None else _DEFAULT_TTL
    entry = MemoryEntry(
        session_id=payload.session_id,
        agent_id=payload.agent_id,
        key=payload.key,
        value=payload.value,
        tags=payload.tags,
        ttl_seconds=ttl,
    )
    return await storage.store_memory(entry, propagate_to_parent=payload.propagate_to_parent)


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
    query: MemoryQuery,
    storage: MemoryStorage = Depends(get_storage),
    include_lineage: bool = Query(False, description="If True, also query parent sessions in the lineage"),
):
    if include_lineage and query.session_id:
        entries = await storage.query_memories_lineage(
            session_id=query.session_id,
            agent_id=query.agent_id,
            tags=query.tags,
            keys=query.keys,
            limit=query.limit,
            offset=query.offset,
        )
    else:
        entries = await storage.query_memories(
            session_id=query.session_id,
            agent_id=query.agent_id,
            tags=query.tags,
            keys=query.keys,
            limit=query.limit,
            offset=query.offset,
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
