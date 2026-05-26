"""Memory Bridge concept visualization page.

Serves the animated agent memory architecture diagram
showing robotic agents storing/retrieving from neural memory.
"""

import logging
import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/concept", tags=["concept"])


@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=True)
async def get_concept_page():
    """Serve the Memory Bridge concept visualization."""
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    html_path = os.path.join(static_dir, "concept.html")
    if not os.path.exists(html_path):
        return HTMLResponse(
            content="<h1>Concept page not found</h1>",
            status_code=200,
        )
    with open(html_path) as f:
        content = f.read()
    return HTMLResponse(content=content)
