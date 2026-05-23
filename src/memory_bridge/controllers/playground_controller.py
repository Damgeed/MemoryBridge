"""Memory Bridge playground page.

Serves the interactive playground HTML that lets users
test memory operations, search, and visualize their data.
"""

import logging
import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from starlette.responses import Response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/playground", tags=["playground"])


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
