"""Business logic for agent-to-agent handoff."""

import logging
from typing import Optional

from ..handoff import HandoffProtocol, HandoffResult
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)


class HandoffService:
    """Service layer for agent handoff operations.

    Delegates to HandoffProtocol for the actual protocol logic.
    Adds auth context, project scoping, and metering.
    """

    def __init__(self, repo: MemoryRepository, metering=None):
        self.repo = repo
        self.metering = metering
        self._protocol = HandoffProtocol(repo)

    async def prepare_handoff(
        self,
        from_agent_id: str,
        to_agent_id: str,
        session_id: str,
        handoff_type: str = "full",
        include_tags: Optional[list[str]] = None,
        project: Optional[str] = None,
    ) -> HandoffResult:
        """Prepare context for agent handoff."""
        return await self._protocol.prepare_handoff(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            session_id=session_id,
            handoff_type=handoff_type,
            include_tags=include_tags or [],
        )

    async def execute_handoff(
        self,
        from_agent_id: str,
        to_agent_id: str,
        session_id: str,
        handoff_type: str = "full",
        include_tags: Optional[list[str]] = None,
        project: Optional[str] = None,
    ) -> HandoffResult:
        """Execute agent-to-agent handoff."""
        return await self._protocol.execute_handoff(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            session_id=session_id,
            handoff_type=handoff_type,
            include_tags=include_tags or [],
        )
