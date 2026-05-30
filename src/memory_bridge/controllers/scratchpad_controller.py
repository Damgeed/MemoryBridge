"""Scratchpad API endpoints — temporary collaborative workspaces for agents.

Agents can create scratchpads, read them, append content, delete them,
and list active ones within a project.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..dependencies import get_storage
from ..models import Scratchpad, ScratchpadAppend, ScratchpadCreate
from ..repository import MemoryRepository
from ..services.scratchpad_service import ScratchpadService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scratchpads")


async def get_scratchpad_service(
    repo: MemoryRepository = Depends(get_storage),
) -> ScratchpadService:
    """Dependency: instantiate ScratchpadService from the current repository."""
    return ScratchpadService(repo=repo)


@router.post("", response_model=Scratchpad, status_code=201)
async def create_scratchpad(
    payload: ScratchpadCreate,
    request: Request,
    service: ScratchpadService = Depends(get_scratchpad_service),
):
    """Create a new scratchpad.

    The initial content is stored as the first entry. Additional entries
    can be appended via POST /scratchpads/{id}/append.
    """
    project_id = payload.project_id or getattr(request.state, "project_id", "")
    try:
        pad = await service.create_scratchpad(
            session_id=payload.session_id,
            agent_id=payload.agent_id,
            project_id=project_id,
            content=payload.content,
            ttl_seconds=payload.ttl_seconds,
        )
        return pad
    except Exception as e:
        logger.exception("Failed to create scratchpad")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/active")
async def list_active_scratchpads(
    request: Request,
    project_id: Optional[str] = Query(None, description="Project scope filter"),
    service: ScratchpadService = Depends(get_scratchpad_service),
):
    """List all active (non-expired) scratchpads for a project.

    If no project_id is provided, falls back to the request's project scope.
    """
    pid = project_id or getattr(request.state, "project_id", "")
    try:
        pads = await service.list_active_scratchpads(pid)
        return {"scratchpads": pads, "count": len(pads)}
    except Exception as e:
        logger.exception("Failed to list active scratchpads")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{id}", response_model=Scratchpad)
async def get_scratchpad(
    id: str,
    service: ScratchpadService = Depends(get_scratchpad_service),
):
    """Get a scratchpad by ID.

    Returns 404 if the scratchpad does not exist or has expired.
    """
    pad = await service.get_scratchpad(id)
    if pad is None:
        raise HTTPException(status_code=404, detail="Scratchpad not found or expired")
    return pad


@router.post("/{id}/append", response_model=Scratchpad)
async def append_to_scratchpad(
    id: str,
    payload: ScratchpadAppend,
    service: ScratchpadService = Depends(get_scratchpad_service),
):
    """Append content to a scratchpad.

    The agent is recorded as a contributor. The scratchpad's TTL is
    refreshed on each append (extended from current time).
    """
    try:
        pad = await service.append_to_scratchpad(
            id=id,
            agent_id=payload.agent_id,
            content=payload.content,
        )
        return pad
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Failed to append to scratchpad %s", id)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{id}")
async def delete_scratchpad(
    id: str,
    service: ScratchpadService = Depends(get_scratchpad_service),
):
    """Delete a scratchpad by ID.

    Returns 404 if the scratchpad does not exist.
    """
    try:
        await service.delete_scratchpad(id)
        return {"deleted": True, "id": id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Failed to delete scratchpad %s", id)
        raise HTTPException(status_code=500, detail=str(e))
