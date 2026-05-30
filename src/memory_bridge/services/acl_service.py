"""ACL (Access Control List) service for per-agent memory permissions.

Supports two permission modes:
1. **Modern (scope-based):** Uses ``scope`` field ("read", "write", "admin")
   with hierarchical resolution (read < write < admin).
2. **Legacy (backward compat):** Falls back to individual ``can_read`` /
   ``can_write`` / ``can_delete`` flags when no scope is set.

Default behaviour (backwards compatible):
- No ACL rule set for an agent -> full access (read, write)
- Once a rule is set, it overrides the default
"""

import logging
from typing import Optional

from ..models import AgentPermission, scope_to_level, scope_implies
from ..repository import MemoryRepository

logger = logging.getLogger(__name__)

# Scope hierarchy constants for readability
SCOPE_READ = "read"
SCOPE_WRITE = "write"
SCOPE_ADMIN = "admin"


class ACLService:
    """Checks agent-level permissions for memory operations.

    Permission resolution (in order):
    1. If no permission rule exists for the agent (+ project), all operations
       are allowed (backward compatible default).
    2. If a rule exists with ``scope`` set, the scope hierarchy determines
       access (read < write < admin).
    3. If ``scope`` is not set (None), the legacy ``can_read`` / ``can_write`` /
       ``can_delete`` flags are used.
    """

    def __init__(self, storage: MemoryRepository):
        self._storage = storage

    # ── Core scope check ───────────────────────────────────────────────────

    async def check_scope(
        self, agent_id: str, required_scope: str, project: Optional[str] = None
    ) -> bool:
        """Return True if the agent's scope level meets the required scope.

        ``required_scope`` must be one of ``"read"``, ``"write"``, ``"admin"``.

        Hierarchy: read < write < admin.
        - Admin implies read + write + delete + manage.
        - If no permission rule exists, returns True (full access default).
        - If scope is not set on the rule, falls back to individual booleans.
        """
        perm = await self._storage.get_agent_permission(agent_id, project)
        if perm is None:
            return True  # Default: full access

        # If scope is set, use hierarchy
        if perm.scope is not None:
            return scope_implies(perm.scope.value, required_scope)

        # Fall back to legacy boolean flags
        if required_scope == SCOPE_READ:
            return perm.can_read
        elif required_scope == SCOPE_WRITE:
            return perm.can_write
        elif required_scope == SCOPE_ADMIN:
            return perm.can_delete
        return False

    async def require_scope(
        self, agent_id: str, required_scope: str, project: Optional[str] = None
    ) -> None:
        """Raise PermissionError if the agent's scope is insufficient."""
        if not await self.check_scope(agent_id, required_scope, project):
            raise PermissionError(
                f"Agent '{agent_id}' does not have '{required_scope}' scope "
                f"for project '{project}'"
            )

    # ── Agent-type whitelist check ─────────────────────────────────────────

    async def check_agent_type(
        self, agent_id: str, agent_type: str, project: Optional[str] = None
    ) -> bool:
        """Return True if the agent_type is allowed by the permission rule.

        If ``allowed_agent_types`` is None or empty, all agent types are
        permitted (default). Otherwise, only listed types are allowed.
        """
        perm = await self._storage.get_agent_permission(agent_id, project)
        if perm is None:
            return True  # Default: full access
        if not perm.allowed_agent_types:
            return True  # No whitelist means all types allowed
        return agent_type in perm.allowed_agent_types

    async def require_agent_type(
        self, agent_id: str, agent_type: str, project: Optional[str] = None
    ) -> None:
        """Raise PermissionError if the agent_type is not whitelisted."""
        if not await self.check_agent_type(agent_id, agent_type, project):
            raise PermissionError(
                f"Agent type '{agent_type}' is not allowed for agent "
                f"'{agent_id}' in project '{project}'"
            )

    # ── Legacy boolean check methods (backward compat) ─────────────────────

    async def check_read(self, agent_id: str, project: Optional[str] = None) -> bool:
        """Return True if the agent is allowed to **read** memories."""
        return await self.check_scope(agent_id, SCOPE_READ, project)

    async def check_write(self, agent_id: str, project: Optional[str] = None) -> bool:
        """Return True if the agent is allowed to **write** (store) memories."""
        return await self.check_scope(agent_id, SCOPE_WRITE, project)

    async def check_delete(self, agent_id: str, project: Optional[str] = None) -> bool:
        """Return True if the agent is allowed to **delete** memories."""
        return await self.check_scope(agent_id, SCOPE_ADMIN, project)

    # ── Guard methods (raise on denial) ────────────────────────────────────

    async def require_read(self, agent_id: str, project: Optional[str] = None) -> None:
        """Raise PermissionError if the agent cannot read."""
        await self.require_scope(agent_id, SCOPE_READ, project)

    async def require_write(self, agent_id: str, project: Optional[str] = None) -> None:
        """Raise PermissionError if the agent cannot write."""
        await self.require_scope(agent_id, SCOPE_WRITE, project)

    async def require_delete(self, agent_id: str, project: Optional[str] = None) -> None:
        """Raise PermissionError if the agent cannot delete."""
        await self.require_scope(agent_id, SCOPE_ADMIN, project)
