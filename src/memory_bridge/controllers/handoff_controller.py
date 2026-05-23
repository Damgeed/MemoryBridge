"""Agent-to-agent handoff endpoints.

Uses HandoffService which delegates to HandoffProtocol for
the actual protocol logic with added auth scoping.
"""

import logging

from fastapi import APIRouter, Depends, Request

from ..dependencies import get_storage
from ..models import HandoffPayload
from ..services.handoff_service import HandoffService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/handoff")


async def get_handoff_service():
    """Dependency: instantiate HandoffService from the current repository."""
    repo = await get_storage()
    return HandoffService(repo=repo)


@router.post("/prepare")
async def prepare_handoff(
    payload: HandoffPayload,
    request: Request,
    service: HandoffService = Depends(get_handoff_service),
):
    """Prepare context for agent-to-agent handoff, scoped to project."""
    project = getattr(request.state, "project_id", None)
    result = await service.prepare_handoff(
        from_agent_id=payload.from_agent_id,
        to_agent_id=payload.to_agent_id,
        session_id=payload.session_id,
        handoff_type=payload.handoff_type,
        include_tags=payload.include_tags,
        project=project,
    )
    return {
        "success": result.success,
        "summary": result.summary,
        "context": result.context,
        "warnings": result.warnings,
    }


@router.post("/execute")
async def execute_handoff(
    payload: HandoffPayload,
    request: Request,
    service: HandoffService = Depends(get_handoff_service),
):
    """Execute agent-to-agent handoff (prepare + store for receiving agent), scoped to project."""
    project = getattr(request.state, "project_id", None)
    result = await service.execute_handoff(
        from_agent_id=payload.from_agent_id,
        to_agent_id=payload.to_agent_id,
        session_id=payload.session_id,
        handoff_type=payload.handoff_type,
        include_tags=payload.include_tags,
        project=project,
    )
    return {
        "success": result.success,
        "summary": result.summary,
        "context": result.context,
        "warnings": result.warnings,
    }
