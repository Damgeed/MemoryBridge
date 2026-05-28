"""Memory Bridge playground page.

Serves the interactive playground HTML that lets users
test memory operations, search, and visualize their data.
Also provides a demo-only endpoint to clear all memories.
"""

import logging
import os

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..dependencies import get_storage
from ..repository.s3_store import S3Store
from ..services.memory_service import MemoryService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/playground", tags=["playground"])


async def get_playground_service():
    """Dependency: instantiate MemoryService for playground operations."""
    repo = await get_storage()
    s3_store = S3Store()
    return MemoryService(repo=repo, s3_store=s3_store)


@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=True)
async def get_playground_page():
    """Serve the interactive memory bridge playground page."""
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    html_path = os.path.join(static_dir, "playground.html")
    if not os.path.exists(html_path):
        return HTMLResponse(
            content="<h1>Playground not found</h1><p>Run <code>pip install -e '.[dev]'</code> to install the static assets.</p>",
            status_code=200,
        )
    with open(html_path) as f:
        content = f.read()
    return Response(
        content=content,
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.delete("/clear")
async def clear_playground_memories(
    request: Request,
    service: MemoryService = Depends(get_playground_service),
):
    """Delete all memories accessible in the current auth scope (demo only).

    This is a demo convenience endpoint — it queries all memories for the
    authenticated project scope and deletes them one by one. Not intended
    for production bulk operations.
    """
    project = getattr(request.state, "project_id", None)

    # Query all memories in this project scope
    entries = await service.query_memories(
        limit=10000,
        project=project,
    )

    if not entries:
        return JSONResponse({"deleted": 0, "message": "No memories to clear."})

    deleted_count = 0
    for entry in entries:
        try:
            await service.delete_memory(entry.id)
            deleted_count += 1
        except Exception as e:
            logger.warning("Failed to delete memory %s: %s", entry.id, e)

    logger.info(
        "Playground clear: deleted %d/%d memories (project=%s)",
        deleted_count,
        len(entries),
        project or "(default)",
    )
    return JSONResponse({
        "deleted": deleted_count,
        "total": len(entries),
        "message": f"Cleared {deleted_count} of {len(entries)} memories.",
    })
