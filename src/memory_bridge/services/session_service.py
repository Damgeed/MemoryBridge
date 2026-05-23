"""Business logic for session lifecycle management."""

import logging
from typing import Optional

from ..models import Session
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)


class SessionService:
    """Service layer for session operations.

    Responsibilities:
    - Session CRUD with auth context and project scoping
    - Lineage traversal
    - Tier limit checks (max sessions per project)
    - Cascade deletes
    """

    def __init__(self, repo: MemoryRepository, metering=None):
        self.repo = repo
        self.metering = metering

    async def create_session(
        self,
        session: Session,
        project: Optional[str] = None,
    ) -> Session:
        """Create a session with project scoping."""
        if project and session.project is None:
            session.project = project
        result = await self.repo.store_session(session)
        return result

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        return await self.repo.get_session(session_id)

    async def get_lineage(self, session_id: str) -> list[str]:
        """Get all ancestor session IDs."""
        return await self.repo.get_session_lineage(session_id)
