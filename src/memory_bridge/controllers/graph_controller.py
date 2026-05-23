"""Memory graph visualization endpoint.

Provides D3.js force-directed graph data by extracting
nodes and edges from stored memories and their metadata.
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from ..dependencies import get_storage
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/data")
async def get_memory_graph(
    session_id: str = Query(None),
    project: str = Query(None),
    limit: int = Query(50, ge=1, le=500),
    storage: MemoryRepository = Depends(get_storage),
):
    """Get memory graph data as nodes and edges for D3.js visualization.

    Returns:
        {
            "nodes": [
                {"id": str, "type": "memory"|"session"|"agent"|"tag",
                 "label": str, "group": int, ...},
                ...
            ],
            "edges": [
                {"source": str, "target": str, "type": "belongs_to"|"authored_by"|"tagged"},
                ...
            ]
        }
    """
    nodes = []
    edges = []
    seen_ids = set()

    # Fetch memories
    memories = await storage.query_memories(
        session_id=session_id,
        limit=limit,
        offset=0,
        project=project,
    )

    if not memories:
        return {"nodes": [], "edges": []}

    for mem in memories:
        # --- Memory node ---
        if mem.id not in seen_ids:
            nodes.append({
                "id": mem.id,
                "type": "memory",
                "label": (mem.key[:30] + "..." if len(mem.key) > 30 else mem.key),
                "group": 1,
                "session_id": mem.session_id,
                "agent_id": mem.agent_id,
                "key": mem.key,
                "tags": mem.tags,
            })
            seen_ids.add(mem.id)

        # --- Session node ---
        if mem.session_id and mem.session_id not in seen_ids:
            nodes.append({
                "id": mem.session_id,
                "type": "session",
                "label": f"Session: {mem.session_id[:16]}...",
                "group": 2,
            })
            seen_ids.add(mem.session_id)

        # Edge: memory → session
        if mem.session_id:
            edges.append({
                "source": mem.id,
                "target": mem.session_id,
                "type": "belongs_to",
            })

        # --- Agent node ---
        if mem.agent_id and mem.agent_id not in seen_ids:
            nodes.append({
                "id": mem.agent_id,
                "type": "agent",
                "label": mem.agent_id[:20] + ("..." if len(mem.agent_id) > 20 else ""),
                "group": 3,
            })
            seen_ids.add(mem.agent_id)

        # Edge: memory → agent
        if mem.agent_id:
            edges.append({
                "source": mem.id,
                "target": mem.agent_id,
                "type": "authored_by",
            })

        # --- Tag nodes ---
        for tag in mem.tags:
            tag_id = f"tag:{tag}"
            if tag_id not in seen_ids:
                nodes.append({
                    "id": tag_id,
                    "type": "tag",
                    "label": f"#{tag}",
                    "group": 4,
                })
                seen_ids.add(tag_id)
            edges.append({
                "source": mem.id,
                "target": tag_id,
                "type": "tagged",
            })

    return {"nodes": nodes, "edges": edges}


@router.get("", response_class=HTMLResponse)
async def get_graph_page():
    """Serve the D3.js force-directed graph visualization page."""
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    html_path = os.path.join(static_dir, "graph.html")
    if not os.path.exists(html_path):
        raise HTTPException(status_code=404, detail="Graph page not found")
    with open(html_path) as f:
        return HTMLResponse(content=f.read())
