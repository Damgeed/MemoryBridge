"""Append-only audit logging for Memory Bridge.

Records all significant operations in an immutable audit trail.
Uses SHA-256 chaining for tamper evidence.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from ..repository import MemoryRepository

logger = logging.getLogger(__name__)


class AuditService:
    """Append-only audit logging service.

    Records all significant operations to the audit_log table.
    Each row is SHA-256 chained to the previous row for tamper evidence.
    """

    def __init__(self, repo: Optional[MemoryRepository] = None):
        self.repo = repo

    async def record(
        self,
        action: str,
        actor_type: str = "system",
        actor_id: str = "system",
        resource_type: str = "",
        resource_id: Optional[str] = None,
        project_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> str:
        """Record an audit event.

        Args:
            action: The action performed (e.g., 'memory.create', 'key.revoke')
            actor_type: 'api_key', 'user', or 'system'
            actor_id: Identifier of the actor
            resource_type: Type of resource affected (e.g., 'memory', 'session')
            resource_id: Identifier of the resource
            project_id: Project scope
            ip_address: Client IP address
            details: Additional context as dict

        Returns:
            The audit log entry ID
        """
        if not self.repo:
            logger.warning("Audit log skipped (no repo configured): %s", action)
            return ""

        # Get previous hash for chaining
        previous_hash = await self._get_last_hash()

        entry_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Build the row content for hashing
        row_data = {
            "id": entry_id,
            "timestamp": now,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "project_id": project_id,
            "ip_address": ip_address,
            "details": details or {},
            "previous_hash": previous_hash,
        }

        # Compute hash of this row
        row_json = json.dumps(row_data, sort_keys=True, default=str)
        row_hash = hashlib.sha256(row_json.encode()).hexdigest()

        # Store in DB
        try:
            await self.repo.record_audit_entry(
                id=entry_id,
                timestamp=now,
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                project_id=project_id,
                ip_address=ip_address,
                details=details or {},
                previous_hash=previous_hash,
                hash=row_hash,
            )
            logger.debug("Audit log: %s by %s/%s", action, actor_type, actor_id)
        except Exception as e:
            logger.warning("Failed to write audit log: %s", e)

        return entry_id

    async def _get_last_hash(self) -> Optional[str]:
        """Get the hash of the most recent audit entry."""
        try:
            return await self.repo.get_last_audit_hash()
        except Exception:
            return None

    async def verify_chain(self) -> dict:
        """Verify the integrity of the audit log chain.

        Returns dict with status and count of entries verified.
        """
        try:
            entries = await self.repo.get_all_audit_entries()
            verified = 0
            previous_hash = None

            for entry in entries:
                row_data = {
                    "id": entry["id"],
                    "timestamp": entry["timestamp"],
                    "actor_type": entry["actor_type"],
                    "actor_id": entry["actor_id"],
                    "action": entry["action"],
                    "resource_type": entry["resource_type"],
                    "resource_id": entry["resource_id"],
                    "project_id": entry["project_id"],
                    "ip_address": entry.get("ip_address"),
                    "details": entry.get("details", {}),
                    "previous_hash": previous_hash,
                }
                row_json = json.dumps(row_data, sort_keys=True, default=str)
                expected_hash = hashlib.sha256(row_json.encode()).hexdigest()

                if expected_hash != entry["hash"]:
                    return {"status": "tampered", "failed_at": entry["id"], "verified": verified}

                previous_hash = entry["hash"]
                verified += 1

            return {"status": "verified", "entries_checked": verified}
        except Exception as e:
            return {"status": "error", "message": str(e)}
