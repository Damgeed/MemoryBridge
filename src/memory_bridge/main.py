from fastapi import FastAPI, Depends, HTTPException
from .dependencies import get_storage
from .handoff import HandoffProtocol
from .models import MemoryEntry, MemoryCreate, MemoryQuery, Session, HandoffPayload
from .storage import MemoryStorage

app = FastAPI(
    title="Memory Bridge",
    version="0.1.0",
    description="Cross-session memory persistence for multi-agent teams",
)


@app.on_event("startup")
async def startup():
    storage = await get_storage()
    await storage.initialize()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "memory-bridge"}


# --- Memory CRUD ---

@app.post("/memories", response_model=MemoryEntry)
async def create_memory(
    payload: MemoryCreate,
    storage: MemoryStorage = Depends(get_storage),
):
    entry = MemoryEntry(
        session_id=payload.session_id,
        agent_id=payload.agent_id,
        key=payload.key,
        value=payload.value,
        tags=payload.tags,
    )
    return await storage.store_memory(entry)


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
):
    entries = await storage.query_memories(
        session_id=query.session_id,
        agent_id=query.agent_id,
        tags=query.tags,
        keys=query.keys,
        limit=query.limit,
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
