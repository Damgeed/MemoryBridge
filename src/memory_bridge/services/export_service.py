"""Data export/import for tenant migration.

Allows exporting all project data as JSON and importing it
into another project. Essential for tenant self-service
migration and backup.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..repository import MemoryRepository

logger = logging.getLogger(__name__)

EXPORT_VERSION = "1.0"


class ExportService:
    """Handles data export and import for tenant migration."""

    def __init__(self, repo: MemoryRepository):
        self.repo = repo

    async def export_project(self, project: str) -> dict:
        """Export all data for a project as a JSON-serializable dict."""
        # Export metadata
        metadata = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "export_version": EXPORT_VERSION,
            "project": project,
        }

        # Export all sessions
        # (In production, this would paginate through all sessions)
        sessions = []
        memories = []

        # We need to query all sessions for this project
        # For now, use query_memories to get recent data
        all_memories = await self.repo.query_memories(
            project=project,
            limit=10000,  # Max export size
            offset=0,
        )

        for mem in all_memories:
            memories.append({
                "id": mem.id,
                "session_id": mem.session_id,
                "agent_id": mem.agent_id,
                "key": mem.key,
                "value": mem.value,
                "tags": mem.tags,
                "created_at": mem.created_at.isoformat() if hasattr(mem.created_at, 'isoformat') else str(mem.created_at),
                "updated_at": mem.updated_at.isoformat() if hasattr(mem.updated_at, 'isoformat') else str(mem.updated_at),
                "ttl_seconds": mem.ttl_seconds,
                "project": mem.project,
            })

            # Collect unique sessions
            session_entry = {
                "session_id": mem.session_id,
                "agent_id": mem.agent_id,
            }
            if session_entry not in sessions:
                sessions.append(session_entry)

        return {
            "metadata": metadata,
            "sessions": sessions,
            "memories": memories,
            "total_memories": len(memories),
            "total_sessions": len(sessions),
        }

    async def import_project(self, data: dict, target_project: str) -> dict:
        """Import previously exported data into a project.

        Args:
            data: The export dict (from export_project)
            target_project: The project to import into

        Returns:
            Dict with import results
        """
        from ..models import MemoryEntry, Session

        memories_imported = 0
        sessions_imported = 0
        errors = []

        # Import sessions
        for session_data in data.get("sessions", []):
            try:
                session = Session(
                    session_id=session_data["session_id"],
                    agent_id=session_data.get("agent_id", "imported"),
                    project=target_project,
                )
                await self.repo.store_session(session)
                sessions_imported += 1
            except Exception as e:
                errors.append(f"Session import error: {e}")

        # Import memories
        for mem_data in data.get("memories", []):
            try:
                entry = MemoryEntry(
                    id=mem_data.get("id"),
                    session_id=mem_data["session_id"],
                    agent_id=mem_data.get("agent_id", "imported"),
                    key=mem_data["key"],
                    value=mem_data.get("value", ""),
                    tags=mem_data.get("tags", []),
                    project=target_project,
                )
                await self.repo.store_memory(entry)
                memories_imported += 1
            except Exception as e:
                errors.append(f"Memory import error: {e}")

        return {
            "status": "complete" if not errors else "partial",
            "sessions_imported": sessions_imported,
            "memories_imported": memories_imported,
            "errors": errors,
        }
