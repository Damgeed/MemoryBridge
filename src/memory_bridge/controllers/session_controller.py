"""Session CRUD endpoints.

Uses SessionService for project scoping and session lifecycle management.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_storage
from ..models import Session
from ..services.session_service import SessionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions")


async def get_session_service():
    """Dependency: instantiate SessionService from the current repository."""
    repo = await get_storage()
    return SessionService(repo=repo)


@router.post("", response_model=Session)
async def create_session(
    session: Session,
    service: SessionService = Depends(get_session_service),
):
    """Create a new session."""
    return await service.create_session(session=session)


@router.get("/{session_id}", response_model=Session)
async def get_session(
    session_id: str,
    service: SessionService = Depends(get_session_service),
):
    """Get a session by its ID."""
    session = await service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session
