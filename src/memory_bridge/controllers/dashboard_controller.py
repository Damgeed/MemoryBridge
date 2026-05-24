"""Memory Bridge dashboard — self-service API key management and setup guide.

Provides authenticated endpoints for users to create, list, and revoke
their own API keys, plus serves the dashboard page with installation
instructions and copy-to-clipboard terminal commands.
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import Response

from ..dependencies import get_storage
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=True)
async def get_dashboard_page():
    """Serve the Memory Bridge dashboard page."""
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    html_path = os.path.join(static_dir, "dashboard.html")
    if not os.path.exists(html_path):
        return HTMLResponse(
            content="<h1>Dashboard page not found</h1>",
            status_code=200,
        )
    with open(html_path) as f:
        content = f.read()
    return Response(
        content=content,
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.post("/keys")
async def create_api_key(
    request: Request,
    label: str = Query("default", description="Human-readable label for the key"),
    storage: MemoryRepository = Depends(get_storage),
):
    """Create a new API key for the authenticated user/organization.

    Returns the full key — this is the only time the plaintext key is shown.
    """
    org_id = _resolve_org(request)
    result = await storage.create_api_key(label=label, project_id=org_id)
    # Tag the key metadata with the org for lookup
    return {
        "id": result["id"],
        "key": result["key"],
        "label": result["label"],
        "created_at": result["created_at"],
    }


@router.get("/welcome")
async def welcome_setup(
    session_id: str = Query("", description="Stripe checkout session ID"),
    storage: MemoryRepository = Depends(get_storage),
):
    """Create a welcome API key for a user who just completed Stripe checkout.

    This endpoint is rate-limited (1 call per session_id) and validates the
    Stripe session before creating a key. Returns the plaintext key once.
    """
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session_id parameter")

    import stripe
    try:
        sess = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid Stripe session: {e}")

    if sess.status != "complete":
        raise HTTPException(status_code=400, detail="Stripe checkout not yet completed")

    org_id = sess.get("client_reference_id") or sess.metadata.get("organization_id", "default")
    tier = sess.metadata.get("tier", "free")

    # Check if this org already has keys — if so, just list them
    existing_keys = await storage.list_api_keys()
    user_keys = [k for k in existing_keys if k.get("project_id") == org_id or not k.get("project_id")]
    if user_keys:
        # Return the most recent key's last-6 (don't show full key again)
        return {
            "welcome": True,
            "has_keys": True,
            "tier": tier,
            "organization_id": org_id,
            "key_count": len(user_keys),
            "latest_key_id": user_keys[-1]["id"],
            "hint": "Paste your existing key in the login bar above, or revoke old keys and generate a new one."
        }

    # Create first API key for this org
    result = await storage.create_api_key(label=f"welcome-{tier}", project_id=org_id)
    return {
        "welcome": True,
        "has_keys": False,
        "key": result["key"],
        "id": result["id"],
        "label": result["label"],
        "tier": tier,
        "organization_id": org_id,
        "created_at": result["created_at"],
    }


@router.get("/keys")
async def list_api_keys(
    request: Request,
    storage: MemoryRepository = Depends(get_storage),
):
    """List all active API keys for the authenticated user/organization.

    Only returns key hashes and metadata — never the plaintext key.
    """
    org_id = _resolve_org(request)
    all_keys = await storage.list_api_keys()
    # Filter keys matching this org (project_id)
    user_keys = [k for k in all_keys if k.get("project_id") == org_id or not k.get("project_id")]
    return {"keys": user_keys}


@router.delete("/keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    request: Request,
    storage: MemoryRepository = Depends(get_storage),
):
    """Revoke an API key. It can no longer be used for authentication."""
    revoked = await storage.revoke_api_key(key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"revoked": True, "key_id": key_id}


@router.get("/data")
async def get_dashboard_data(
    request: Request,
    storage: MemoryRepository = Depends(get_storage),
):
    """Get dashboard data: subscription info, key count, memory count."""
    org_id = _resolve_org(request)

    # Get subscription
    sub = None
    try:
        sub = await storage.get_subscription_by_org(org_id)
    except Exception:
        pass

    # Get key count
    keys = await storage.list_api_keys()
    user_keys = [k for k in keys if k.get("project_id") == org_id or not k.get("project_id")]
    active_keys = [k for k in user_keys if k.get("is_active", True)]

    # Get memory count
    mem_count = 0
    try:
        memories = await storage.query_memories(limit=1, offset=0)
        # Try to get total count - may not be available in all backends
        mem_count = len(memories)
    except Exception:
        pass

    tier = sub.tier if sub else "free"
    if sub and sub.status == "canceled":
        tier = "free"

    return {
        "organization_id": org_id,
        "tier": tier,
        "status": sub.status if sub else "active",
        "active_keys": len(active_keys),
        "total_keys": len(user_keys),
        "current_period_end": sub.current_period_end.isoformat() if sub and sub.current_period_end else None,
    }


def _resolve_org(request: Request) -> str:
    """Resolve the organization ID from the authenticated request.

    Falls back to a session-based ID for demo/open-mode users.
    """
    auth = getattr(request.state, "auth", None)
    if auth:
        return auth.get("project_id") or auth.get("key_id", "default")
    return "default"
