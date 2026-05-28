"""Memory CRUD endpoints.

Uses MemoryService for business logic including project scope
resolution, default TTL application, and cache integration.
"""

import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..config import get_settings
from ..dependencies import get_storage
from ..models import MemoryCreate, MemoryEntry, MemoryQuery
from ..repository.s3_store import S3Store
from ..services.embedding_service import EmbeddingService
from ..services.memory_service import MemoryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memories")


async def get_memory_service():
    """Dependency: instantiate MemoryService from the current repository."""
    repo = await get_storage()
    s3_store = S3Store()
    service = MemoryService(repo=repo, s3_store=s3_store)
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
    project = payload.project or getattr(request.state, "project_id", None)

    # Validate value size before processing (fast-fail for HTTP clients)
    value_size = len(json.dumps(payload.value))
    max_value_size = get_settings().max_value_size
    if value_size > max_value_size:
        raise HTTPException(
            status_code=413,
            detail=f"Memory value too large: {value_size} bytes exceeds limit of {max_value_size} bytes",
        )

    return await service.create_memory(
        payload=payload,
        project=project,
        auth_context=auth_context,
    )


@router.get("/search")
async def search_memories(
    request: Request,
    q: str = Query(..., description="Natural language search query"),
    session_id: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    project: Optional[str] = Query(None, description="Project filter (admin use)"),
    limit: int = Query(10, ge=1, le=50),
    service: MemoryService = Depends(get_memory_service),
):
    """Semantic / natural language search across memories.

    Uses the configured embedding provider (sentence-transformers, OpenAI,
    or keyword fallback). Returns results sorted by relevance (score
    descending) with indicators showing whether each match was found
    via semantic vector similarity or keyword fallback.

    Falls back to full-text search when:
    - No embedding model is available (keyword-only provider)
    - No stored embeddings exist yet
    """
    embedding_service = EmbeddingService()
    auth_project = getattr(request.state, "project_id", None)
    resolved_project = project or auth_project

    query_vector = await embedding_service.embed(q)
    provider = embedding_service.provider_name

    if query_vector is None or len(query_vector) == 0:
        # Keyword fallback — use existing FTS / key-based search
        logger.info(
            "Semantic search unavailable (provider=%s), falling back to FTS",
            provider,
        )
        entries = await service.search_memories(
            query=q,
            limit=limit,
            session_id=session_id,
            agent_id=agent_id,
            project=resolved_project,
        )
        results = [
            {
                "memory": e.model_dump(),
                "score": 1.0,
                "matched_by": "keyword",
            }
            for e in entries
        ]
        return {"results": results, "provider": provider}

    # Vector search
    matched_ids = await service.repo.search_by_vector(
        embedding=query_vector,
        limit=limit,
    )

    if not matched_ids:
        # No embeddings stored yet — fall back to FTS
        logger.info("No stored embeddings found, falling back to FTS")
        entries = await service.search_memories(
            query=q,
            limit=limit,
            session_id=session_id,
            agent_id=agent_id,
            project=resolved_project,
        )
        results = [
            {
                "memory": e.model_dump(),
                "score": 1.0,
                "matched_by": "keyword",
            }
            for e in entries
        ]
        return {"results": results, "provider": provider}

    # Fetch full memory entries for matched IDs
    results = []
    for mem_id in matched_ids:
        entry = await service.get_memory(mem_id)
        if entry is None:
            continue

        # Apply session/agent/project filters
        if session_id and entry.session_id != session_id:
            continue
        if agent_id and entry.agent_id != agent_id:
            continue
        if resolved_project and entry.project != resolved_project:
            continue

        # Compute similarity score
        stored_emb = await service.repo.get_embedding(mem_id)
        score = 1.0
        if stored_emb:
            score = embedding_service.cosine_similarity(query_vector, stored_emb)

        results.append({
            "memory": entry.model_dump(),
            "score": round(score, 4),
            "matched_by": "semantic",
        })

    # Sort by score descending
    results.sort(key=lambda r: -r["score"])

    return {"results": results, "provider": provider}


@router.post("/semantic_search")
async def semantic_search_memories(
    request: Request,
    payload: MemoryQuery,
    service: MemoryService = Depends(get_memory_service),
):
    """Search memories by semantic similarity.

    Generates an embedding from the query text (extracted from the first
    key or tag in the payload, or falls back to FTS). Uses pgvector
    on PostgreSQL or brute-force cosine similarity on SQLite.

    Reuses the MemoryQuery model — the 'keys' field acts as the search
    query text, or 'tags' can be used for the query context.
    """
    project = payload.project or getattr(request.state, "project_id", None)

    # Build a search query from the payload
    query_text = " ".join(payload.keys) if payload.keys else ""
    if payload.tags:
        query_text = (query_text + " " + " ".join(payload.tags)).strip()
    if not query_text:
        # Fall back to session_id as a filter context
        query_text = payload.session_id or ""

    entries = await service.search_memories_semantic(
        query=query_text,
        project=project,
        limit=payload.limit,
        offset=payload.offset,
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
    project = query.project or getattr(request.state, "project_id", None)
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
