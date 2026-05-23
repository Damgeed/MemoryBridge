"""Admin API key management endpoints.

Direct repository access since key management is an infrastructure
operation outside the service layer.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_storage
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")


@router.post("/keys")
async def admin_create_api_key(
    label: str = Query(..., description="Human-readable label for the key"),
    project_id: Optional[str] = Query(None, description="Optional project scope"),
    storage: MemoryRepository = Depends(get_storage),
):
    """Create a new API key. The full key is returned only once."""
    return await storage.create_api_key(label=label, project_id=project_id)


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
