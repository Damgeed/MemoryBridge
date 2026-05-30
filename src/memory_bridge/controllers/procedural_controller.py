"""Procedural memory endpoints — record action chains and detect patterns."""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..dependencies import get_storage
from ..models import MemoryEntry
from ..repository import MemoryRepository
from ..services.procedural_service import ProceduralMemoryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/procedural")


async def get_procedural_service(
    repo: MemoryRepository = Depends(get_storage),
) -> ProceduralMemoryService:
    return ProceduralMemoryService(repo=repo)


@router.post("/record")
async def record_action(
    agent_id: str = Query(..., description="Agent performing the action"),
    session_id: str = Query(..., description="Current session ID"),
    action: str = Query(..., description="Action name (e.g. 'search_code', 'analyze_results')"),
    project: Optional[str] = Query(None),
    service: ProceduralMemoryService = Depends(get_procedural_service),
):
    """Record a single action in the agent's session chain.

    Actions accumulate in a chain. Call this for each step your agent takes.
    """
    await service.record_action(
        agent_id=agent_id,
        session_id=session_id,
        action=action,
        project=project,
    )
    return {"recorded": True, "action": action, "session_id": session_id}


@router.post("/finalize")
async def finalize_session(
    session_id: str = Query(..., description="Session to finalize"),
    agent_id: str = Query(..., description="Agent that owns the session"),
    project: Optional[str] = Query(None),
    service: ProceduralMemoryService = Depends(get_procedural_service),
):
    """Finalize a session's action chain and detect patterns.

    Call this when a session ends. If a repeating workflow pattern
    is detected, it's saved as a procedural memory for reuse.
    """
    pattern = await service.finalize_session_chain(
        session_id=session_id,
        agent_id=agent_id,
        project=project,
    )
    if pattern:
        return {
            "finalized": True,
            "pattern_detected": True,
            "pattern": pattern["pattern"],
            "repetition_count": pattern["count"],
        }
    return {"finalized": True, "pattern_detected": False}


@router.get("/patterns")
async def list_patterns(
    project: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    repo: MemoryRepository = Depends(get_storage),
):
    """List all detected procedural patterns, sorted by frequency."""
    patterns = await repo.query_memories(
        tags=["procedural", "pattern"],
        project=project,
        limit=limit,
    )
    # Sort by repetition count descending
    sorted_patterns = []
    for mem in patterns:
        val = mem.value if isinstance(mem.value, dict) else {}
        sorted_patterns.append({
            "pattern": val.get("sequence", []),
            "count": val.get("count", 0),
            "first_seen": val.get("first_seen"),
            "last_seen": val.get("last_seen"),
            "memory_id": mem.id,
        })
    sorted_patterns.sort(key=lambda p: -p["count"])
    return {"patterns": sorted_patterns, "total": len(sorted_patterns)}
