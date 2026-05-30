"""Scratchpad service — temporary collaborative workspaces for agents.

Scratchpads are temporary workspaces where multiple agents can write,
read, and collaborate in real-time. They auto-expire after a configurable
TTL (default 30 minutes).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..models import Scratchpad
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)


class ScratchpadService:
    """Core service for managing scratchpads."""

    def __init__(self, repo: MemoryRepository):
        self.repo = repo

    async def create_scratchpad(
        self,
        session_id: str,
        agent_id: str,
        project_id: str,
        content: str,
        ttl_seconds: int = 1800,
    ) -> Scratchpad:
        """Create a new scratchpad that auto-expires after ttl_seconds.

        The initial content is stored as the first entry in the content list,
        and the creating agent is recorded as the first contributor.
        """
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)
        pad = Scratchpad(
            session_id=session_id,
            agent_id=agent_id,
            project_id=project_id,
            content=[content],
            contributors=[agent_id],
            created_at=now,
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
        )
        created = await self.repo.create_scratchpad(pad)
        logger.info(
            "Created scratchpad %s for session %s (project=%s, TTL=%ds)",
            created.id, session_id, project_id, ttl_seconds,
        )
        return created

    async def get_scratchpad(self, id: str) -> Optional[Scratchpad]:
        """Retrieve a scratchpad by ID. Returns None if not found or expired."""
        return await self.repo.get_scratchpad(id)

    async def append_to_scratchpad(
        self,
        id: str,
        agent_id: str,
        content: str,
    ) -> Scratchpad:
        """Append content to an existing scratchpad.

        The agent is recorded as a contributor if not already in the list.
        Raises ValueError if the scratchpad is not found or expired.
        """
        pad = await self.repo.get_scratchpad(id)
        if pad is None:
            raise ValueError(f"Scratchpad {id} not found or expired")

        pad.content.append(content)
        if agent_id not in pad.contributors:
            pad.contributors.append(agent_id)

        # Extend expires_at to now + original ttl (refresh on update)
        pad.expires_at = datetime.now(timezone.utc) + timedelta(seconds=pad.ttl_seconds)

        updated = await self.repo.update_scratchpad(pad)
        logger.info(
            "Appended to scratchpad %s by agent %s (%d entries total)",
            id, agent_id, len(updated.content),
        )
        return updated

    async def delete_scratchpad(self, id: str) -> None:
        """Delete a scratchpad by ID. Raises ValueError if not found."""
        deleted = await self.repo.delete_scratchpad(id)
        if not deleted:
            raise ValueError(f"Scratchpad {id} not found")
        logger.info("Deleted scratchpad %s", id)

    async def list_active_scratchpads(self, project_id: str) -> list[Scratchpad]:
        """List all active (non-expired) scratchpads for a project."""
        return await self.repo.list_active_scratchpads(project_id)

    async def cleanup_expired(self) -> int:
        """Delete all expired scratchpads. Returns count deleted."""
        count = await self.repo.cleanup_expired_scratchpads()
        if count:
            logger.info("Cleanup: deleted %d expired scratchpads", count)
        return count
