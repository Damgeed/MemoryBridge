"""Admin API endpoints for user/project management and API keys.

Direct repository access for key management since it is an infrastructure
operation outside the service layer. User/project/analytics endpoints
use the AdminService layer.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_storage
from ..repository import MemoryRepository
from ..services.admin_service import AdminService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")


async def get_admin_service():
    repo = await get_storage()
    return AdminService(repo=repo)


# ── API Key Management (direct repository access) ─────────────────────────


@router.post("/keys")
async def admin_create_api_key(
    label: str = Query(..., description="Human-readable label for the key"),
    project_id: Optional[str] = Query(None, description="Optional project scope"),
    scope: Optional[str] = Query(None, description='Permission scope: "read", "write", or "admin". Default: full access'),
    storage: MemoryRepository = Depends(get_storage),
):
    """Create a new API key with optional permission scope.

    The full key is returned only once.
    Scope levels: read < write < admin. If omitted, the key has full access.
    """
    return await storage.create_api_key(label=label, project_id=project_id, scope=scope)


@router.get("/keys")
async def admin_list_api_keys(
    storage: MemoryRepository = Depends(get_storage),
):
    """List all API keys (key hashes only, not the actual keys)."""
    keys = await storage.list_api_keys()
    return {"keys": keys}


@router.delete("/keys/{key_id}")
async def admin_revoke_api_key(
    key_id: str,
    storage: MemoryRepository = Depends(get_storage),
):
    """Revoke an API key. It can no longer be used for authentication."""
    revoked = await storage.revoke_api_key(key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"revoked": True, "key_id": key_id}


# ── User Management ──────────────────────────────────────────────────────


@router.get("/users")
async def list_users(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    service: AdminService = Depends(get_admin_service),
):
    """List registered users."""
    users = await service.list_users(limit=limit, offset=offset)
    return {"users": users, "total": len(users)}


# ── Project Management ───────────────────────────────────────────────────


@router.get("/projects")
async def list_projects(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    service: AdminService = Depends(get_admin_service),
):
    """List all projects."""
    projects = await service.list_projects(limit=limit, offset=offset)
    return {"projects": projects, "total": len(projects)}


# ── Analytics ────────────────────────────────────────────────────────────


@router.get("/analytics")
async def get_analytics(
    service: AdminService = Depends(get_admin_service),
):
    """Get system-wide analytics."""
    return await service.get_analytics()


# ── System Health ────────────────────────────────────────────────────────


@router.get("/health/system")
async def system_health(
    service: AdminService = Depends(get_admin_service),
):
    """Get system health status."""
    return await service.get_system_health()
