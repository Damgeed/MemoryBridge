"""Memory Bridge pricing page.

Serves the pricing page that shows tier plans,
feature comparisons, and FAQ about billing.
"""

import logging
import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from starlette.responses import Response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pricing", tags=["pricing"])


@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=True)
async def get_pricing_page():
    """Serve the Memory Bridge pricing page."""
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    html_path = os.path.join(static_dir, "pricing.html")
    if not os.path.exists(html_path):
        return HTMLResponse(
            content="<h1>Pricing page not found</h1><p>Run <code>pip install -e '.[dev]'</code> to install the static assets.</p>",
            status_code=200,
        )
    with open(html_path) as f:
        content = f.read()
    return Response(
        content=content,
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )
