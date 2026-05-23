"""Memory CRUD endpoints.

Uses MemoryService for business logic including project scope
resolution, default TTL application, and cache integration.
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..dependencies import get_storage
from ..models import MemoryCreate, MemoryEntry, MemoryQuery
from ..services.memory_service import MemoryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memories")


async def get_memory_service():
    """Dependency: instantiate MemoryService from the current repository."""
    repo = await get_storage()
    service = MemoryService(repo=repo)
    # Apply server-side default TTL from env
    default_ttl = int(os.environ.get("MEMORY_BRIDGE_DEFAULT_TTL", "0")) or None
    if default_ttl:
        service._default_ttl = default_ttl
    return service


@router.post("", response_model=MemoryEntry)
async def create_memory(
    payload: MemoryCreate,
    request: Request,
    service: MemoryService = Depends(get_memory_service),
):
    """Create a memory entry.

    Inherits project scope from auth if not explicitly set.
    Applies server-wide default TTL if configured.
    """
    auth_context = getattr(request.state, "auth", None)
    project = payload.project
    return await service.create_memory(
        payload=payload,
        project=project,
        auth_context=auth_context,
    )


@router.get("/search")
async def search_memories(
    request: Request,
    q: str = Query(..., description="Full-text search query"),
    session_id: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    service: MemoryService = Depends(get_memory_service),
):
    """Full-text search across memories."""
    project = None
    if hasattr(request.state, "auth") and request.state.auth:
        project = request.state.auth.get("project_id")
    entries = await service.search_memories(
        query=q,
        limit=limit,
        offset=offset,
        session_id=session_id,
        agent_id=agent_id,
        project=project,
    )
    return {"entries": [e.model_dump() for e in entries], "total": len(entries)}


@router.get("/{memory_id}", response_model=MemoryEntry)
async def get_memory(
    memory_id: str,
    service: MemoryService = Depends(get_memory_service),
):
    """Get a memory by its ID."""
    entry = await service.get_memory(memory_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return entry


@router.post("/query")
async def query_memories(
    request: Request,
    query: MemoryQuery,
    service: MemoryService = Depends(get_memory_service),
    include_lineage: bool = Query(
        False,
        description="If True, also query parent sessions in the lineage",
    ),
):
    """Query memories with optional filters and lineage traversal."""
    project = query.project
    if project is None and hasattr(request.state, "auth") and request.state.auth:
        project = request.state.auth.get("project_id")
    entries = await service.query_memories(
        session_id=query.session_id,
        agent_id=query.agent_id,
        tags=query.tags,
        keys=query.keys,
        limit=query.limit,
        offset=query.offset,
        project=project,
        include_lineage=include_lineage,
    )
    return {"entries": [e.model_dump() for e in entries], "total": len(entries)}


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    service: MemoryService = Depends(get_memory_service),
):
    """Delete a memory by its ID."""
    deleted = await service.delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}
