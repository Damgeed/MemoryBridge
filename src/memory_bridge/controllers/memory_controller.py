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
from ..models import (
    ExtractFactsRequest,
    MemoryCreate,
    MemoryEntry,
    MemoryQuery,
    ScoreMemoriesRequest,
    ScoreMemoriesResponse,
    ScoredMemoryResult,
)
from ..repository.s3_store import S3Store
from ..services.acl_service import ACLService
from ..services.embedding_service import EmbeddingService
from ..services.fact_extraction_service import FactExtractionService
from ..services.memory_service import MemoryService
from ..services.scoring_service import MemoryScoringService

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

    # ACL check: verify the agent has write permission (scope-based)
    acl = ACLService(storage=service.repo)
    try:
        await acl.require_scope(agent_id=payload.agent_id, required_scope="write", project=project)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    # ACL check: verify agent_type is whitelisted if applicable
    agent_type = getattr(request.state, "agent_type", None) or "default"
    try:
        await acl.require_agent_type(agent_id=payload.agent_id, agent_type=agent_type, project=project)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    return await service.create_memory(
        payload=payload,
        project=project,
        auth_context=auth_context,
    )


@router.post("/extract")
async def extract_facts(
    payload: ExtractFactsRequest,
    request: Request,
    repo=Depends(get_storage),
):
    """Extract atomic facts from raw text using an LLM provider.

    Returns extracted facts with categories, confidence scores, and entities.
    Optionally stores each fact as a separate MemoryEntry when store_facts=true.

    When no LLM provider is configured, returns the raw text as a single
    fact with category 'other'.
    """
    # Validate store_facts requirements
    if payload.store_facts:
        if not payload.agent_id:
            raise HTTPException(
                status_code=422,
                detail="agent_id is required when store_facts=true",
            )
        if not payload.session_id:
            raise HTTPException(
                status_code=422,
                detail="session_id is required when store_facts=true",
            )

    # Extract facts
    extractor = FactExtractionService()
    facts = await extractor.extract_facts(
        text=payload.text,
        source_key=payload.source_key or "",
        max_facts=payload.max_facts,
    )

    # Optionally store each fact as a MemoryEntry
    stored_count = 0
    if payload.store_facts and facts:
        all_tags = list(payload.tags)
        if "extracted" not in all_tags:
            all_tags.append("extracted")

        for idx, fact_data in enumerate(facts):
            source = payload.source_key or "extracted"
            fact_key = "fact:{}:{}".format(source, idx)
            entry = MemoryEntry(
                session_id=payload.session_id,
                agent_id=payload.agent_id,
                key=fact_key,
                value={
                    "fact": fact_data["fact"],
                    "category": fact_data["category"],
                    "confidence": fact_data["confidence"],
                    "entities": fact_data["entities"],
                },
                tags=all_tags,
            )
            try:
                await repo.store_memory(entry)
                stored_count += 1
            except Exception as e:
                logger.warning(
                    "Failed to store fact %d (key=%s): %s",
                    idx,
                    fact_key,
                    e,
                )

    return {
        "facts": facts,
        "provider": extractor.provider_name,
        "stored_count": stored_count,
    }


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


@router.post("/score", response_model=ScoreMemoriesResponse)
async def score_memories(
    request: Request,
    payload: ScoreMemoriesRequest,
    service: MemoryService = Depends(get_memory_service),
):
    """Score and rank memories by recency, relevance, and importance.

    Accepts optional memory IDs to score specific memories, or filters
    (session_id, agent_id) to select memories, then ranks them using
    a composite scoring function.

    Weights can be customized via the `weights` parameter:
    ```json
    {"recency": 0.3, "relevance": 0.5, "importance": 0.2}
    ```
    """
    project = getattr(request.state, "project_id", None)
    scorer = MemoryScoringService()

    # Fetch memories to score
    if payload.memories:
        # Score specific memories by ID
        raw_memories = []
        for mem_id in payload.memories:
            entry = await service.get_memory(mem_id, project=project)
            if entry is not None:
                raw_memories.append(entry)
        # If no memories found, return empty
        if not raw_memories:
            return ScoreMemoriesResponse(results=[], count=0)
    else:
        # Fetch via filters
        raw_memories = await service.query_memories(
            session_id=payload.session_id,
            agent_id=payload.agent_id,
            limit=payload.limit,
            project=project,
        )

    # Score and rank
    scored = await scorer.score_memories_async(
        memories=raw_memories,
        query_context=payload.query,
        weights=payload.weights,
    )

    # Apply limit and build response
    if payload.limit and len(scored) > payload.limit:
        scored = scored[:payload.limit]

    results = [
        ScoredMemoryResult(
            memory=s["memory"],
            score=s["score"],
            recency_score=s["recency_score"],
            relevance_score=s["relevance_score"],
            importance_score=s["importance_score"],
        )
        for s in scored
    ]

    return ScoreMemoriesResponse(results=results, count=len(results))


@router.get("/{memory_id}", response_model=MemoryEntry)
async def get_memory(
    memory_id: str,
    request: Request,
    service: MemoryService = Depends(get_memory_service),
):
    """Get a memory by its ID."""
    entry = await service.get_memory(memory_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Memory not found")

    # ACL check: verify the agent has read scope or better
    project = getattr(request.state, "project_id", None)
    acl = ACLService(storage=service.repo)
    try:
        await acl.require_scope(agent_id=entry.agent_id, required_scope="read", project=project)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

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
    request: Request,
    agent_id: Optional[str] = Query(None, description="Agent ID for ACL permission check"),
    service: MemoryService = Depends(get_memory_service),
):
    """Delete a memory by its ID."""
    # ACL check: verify the agent has admin scope (or write, design decision)
    project = getattr(request.state, "project_id", None)
    if agent_id:
        acl = ACLService(storage=service.repo)
        try:
            # Delete requires admin scope (which includes read+write+delete+manage)
            await acl.require_scope(agent_id=agent_id, required_scope="admin", project=project)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))

    deleted = await service.delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}
