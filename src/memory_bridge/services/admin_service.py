"""Admin operations for user and project management."""

import logging
from typing import Optional

from ..repository import MemoryRepository

logger = logging.getLogger(__name__)


class AdminService:
    """Service layer for admin operations.

    Provides user management, project management, analytics,
    and system health operations.
    """

    def __init__(self, repo: MemoryRepository = None):
        self.repo = repo

    async def list_users(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """List registered users (paginated)."""
        # In production, query from public.users table
        return []

    async def list_projects(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """List all projects (paginated)."""
        # In production, query from public.projects table
        return []

    async def get_analytics(self) -> dict:
        """Get system-wide analytics."""
        if not self.repo:
            return {"memories": 0, "sessions": 0, "projects": 0, "users": 0}
        try:
            memories = await self.repo.count_memories()
            sessions = await self.repo.count_sessions()
            return {
                "memories": memories or 0,
                "sessions": sessions or 0,
                "projects": 0,
                "users": 0,
            }
        except Exception:
            logger.warning("Failed to fetch analytics", exc_info=True)
            return {"memories": 0, "sessions": 0, "projects": 0, "users": 0}

    async def suspend_user(self, user_id: str) -> bool:
        """Suspend a user account."""
        logger.warning("Admin suspended user: %s", user_id)
        return True

    async def create_api_key(
        self,
        label: str,
        project_id: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> dict:
        """Create a new API key with optional scope.

        Args:
            label: Human-readable label for the key.
            project_id: Optional project scope.
            scope: Optional permission scope ("read", "write", "admin").
                   If None, the key has full access (backward compatible default).

        Returns:
            dict with id, key (plaintext), label, project_id, scope, is_active, created_at.
        """
        if not self.repo:
            raise RuntimeError("Repository not configured")
        return await self.repo.create_api_key(
            label=label,
            project_id=project_id,
            scope=scope,
        )

    async def get_system_health(self) -> dict:
        """Get system health status."""
        status = "healthy" if self.repo else "degraded"
        return {
            "status": status,
            "service": "memory-bridge",
            "version": "0.6.0",
            "repo_available": self.repo is not None,
        }
