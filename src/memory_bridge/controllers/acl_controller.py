"""ACL (Access Control List) endpoints for managing per-agent permissions.

All endpoints require authentication (API key or JWT). The project scope
is resolved from the authenticated context.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from ..dependencies import get_storage
from ..models import AgentPermission, AgentPermissionUpdate
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/permissions")


@router.get("")
async def list_permissions(
    request: Request,
    repo: MemoryRepository = Depends(get_storage),
):
    """List all agent permission rules for the authenticated project."""
    project = getattr(request.state, "project_id", None)
    permissions = await repo.list_agent_permissions(project=project)
    return {"permissions": [p.model_dump() for p in permissions]}


@router.put("/{agent_id}")
async def set_permission(
    agent_id: str,
    request: Request,
    body: AgentPermissionUpdate,
    repo: MemoryRepository = Depends(get_storage),
):
    """Set or update permission for an agent."""
    project = getattr(request.state, "project_id", None)

    # Get existing permission to merge with partial update
    existing = await repo.get_agent_permission(agent_id, project)

    now = datetime.now(timezone.utc)
    perm = AgentPermission(
        agent_id=agent_id,
        project=project,
        can_read=body.can_read if body.can_read is not None else (existing.can_read if existing else True),
        can_write=body.can_write if body.can_write is not None else (existing.can_write if existing else True),
        can_delete=body.can_delete if body.can_delete is not None else (existing.can_delete if existing else False),
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    await repo.set_agent_permission(perm)
    return {"status": "updated", "permission": perm.model_dump()}


@router.delete("/{agent_id}")
async def delete_permission(
    agent_id: str,
    request: Request,
    repo: MemoryRepository = Depends(get_storage),
):
    """Remove a permission rule for an agent (reverts to default full access)."""
    project = getattr(request.state, "project_id", None)
    deleted = await repo.delete_agent_permission(agent_id, project)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"No permission rule found for agent '{agent_id}'",
        )
    return {"status": "deleted"}
