"""ACL (Access Control List) service for per-agent memory permissions.

Default behavior (backwards compatible):
- No ACL rule set for an agent -> full access (read, write)
- Once a rule is set, it overrides the default
"""

import logging
from typing import Optional

from ..models import AgentPermission
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)


class ACLService:
    """Checks agent-level permissions for memory operations.

    Permission resolution:
    - If no permission rule exists for the agent (+ project), all operations
      are allowed (backward compatible default).
    - If a rule exists, the explicit ``can_read`` / ``can_write`` /
      ``can_delete`` flags determine access.
    """

    def __init__(self, storage: MemoryRepository):
        self._storage = storage

    async def check_read(self, agent_id: str, project: Optional[str] = None) -> bool:
        """Return True if the agent is allowed to **read** memories."""
        perm = await self._storage.get_agent_permission(agent_id, project)
        if perm is None:
            return True  # Default: full access
        return perm.can_read

    async def check_write(self, agent_id: str, project: Optional[str] = None) -> bool:
        """Return True if the agent is allowed to **write** (store) memories."""
        perm = await self._storage.get_agent_permission(agent_id, project)
        if perm is None:
            return True  # Default: full access
        return perm.can_write

    async def check_delete(self, agent_id: str, project: Optional[str] = None) -> bool:
        """Return True if the agent is allowed to **delete** memories."""
        perm = await self._storage.get_agent_permission(agent_id, project)
        if perm is None:
            return True  # Default: full access
        return perm.can_delete

    # ── Guard methods (raise on denial) ──────────────────────────────────────

    async def require_read(self, agent_id: str, project: Optional[str] = None) -> None:
        """Raise PermissionError if the agent cannot read."""
        if not await self.check_read(agent_id, project):
            raise PermissionError(
                f"Agent '{agent_id}' does not have read permission "
                f"for project '{project}'"
            )

    async def require_write(self, agent_id: str, project: Optional[str] = None) -> None:
        """Raise PermissionError if the agent cannot write."""
        if not await self.check_write(agent_id, project):
            raise PermissionError(
                f"Agent '{agent_id}' does not have write permission "
                f"for project '{project}'"
            )

    async def require_delete(self, agent_id: str, project: Optional[str] = None) -> None:
        """Raise PermissionError if the agent cannot delete."""
        if not await self.check_delete(agent_id, project):
            raise PermissionError(
                f"Agent '{agent_id}' does not have delete permission "
                f"for project '{project}'"
            )
