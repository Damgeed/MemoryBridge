"""Agent-to-agent context handoff with guardrails."""

import asyncio
import logging
from typing import Any, Optional

from .models import HandoffPayload, MemoryEntry, MemoryQuery
from .storage import MemoryStorage

logger = logging.getLogger(__name__)


class HandoffResult:
    """Result of a handoff operation."""

    def __init__(
        self,
        success: bool,
        summary: str,
        context: dict[str, Any],
        warnings: list[str] = None,
    ):
        self.success = success
        self.summary = summary
        self.context = context
        self.warnings = warnings or []


class HandoffError(Exception):
    """Error raised when a handoff operation cannot proceed."""

    def __init__(self, detail: str, status_code: int = 409):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


class HandoffGuardrails:
    """Guardrails for safe agent-to-agent context handoff."""

    MAX_CONTEXT_SIZE = 100_000  # characters
    BLOCKED_KEYS = {"credentials", "api_key", "token", "password", "secret"}

    @classmethod
    def validate_payload(cls, payload: HandoffPayload) -> list[str]:
        """Validate handoff payload. Returns list of warnings."""
        warnings = []

        if payload.from_agent_id == payload.to_agent_id:
            warnings.append("Source and destination agents are identical")

        context_size = len(str(payload.context))
        if context_size > cls.MAX_CONTEXT_SIZE:
            warnings.append(
                f"Context size ({context_size} chars) exceeds limit "
                f"({cls.MAX_CONTEXT_SIZE})"
            )

        # Check for blocked keys
        for key in payload.context:
            if key.lower() in cls.BLOCKED_KEYS:
                warnings.append(f"Context contains potentially sensitive key: '{key}'")

        if payload.handoff_type not in ("full", "summary", "selective"):
            warnings.append(f"Unknown handoff type: {payload.handoff_type}")

        return warnings

    @classmethod
    def sanitize_context(cls, context: dict[str, Any]) -> dict[str, Any]:
        """Remove sensitive keys from context."""
        return {
            k: v
            for k, v in context.items()
            if k.lower() not in cls.BLOCKED_KEYS
        }


class HandoffProtocol:
    """Orchestrates agent-to-agent handoff with guardrails."""

    def __init__(self, storage: MemoryStorage):
        self.storage = storage
        self._session_locks: dict[str, asyncio.Lock] = {}

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create a per-session lock."""
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    async def _cleanup_stale_locks(self) -> None:
        """Remove locks whose sessions no longer exist in storage.

        Prevents unbounded growth of ``_session_locks`` when sessions
        are deleted externally or expire naturally.
        """
        stale = []
        for sid in list(self._session_locks):
            session = await self.storage.get_session(sid)
            if session is None:
                stale.append(sid)
        for sid in stale:
            # Only remove if the lock is not currently acquired
            lock = self._session_locks[sid]
            if not lock.locked():
                del self._session_locks[sid]
        if stale:
            logger.debug("Cleaned up %d stale session locks", len(stale))

    async def _acquire_session_lock(self, session_id: str, timeout: float = 5.0) -> None:
        """Acquire the per-session lock with a timeout.

        Raises HandoffError if the lock cannot be acquired within the timeout.
        """
        lock = self._get_session_lock(session_id)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
        except asyncio.TimeoutError:
            raise HandoffError(
                detail=f"Session '{session_id}' is busy with another handoff. "
                       f"Could not acquire lock within {timeout}s.",
                status_code=409,
            )

    async def prepare_handoff(
        self,
        from_agent_id: str,
        to_agent_id: str,
        session_id: str,
        handoff_type: str = "summary",
        include_tags: Optional[list[str]] = None,
    ) -> HandoffResult:
        """Prepare context for handoff between agents."""
        await self._acquire_session_lock(session_id)
        try:
            return await self._prepare_handoff_internal(
                from_agent_id=from_agent_id,
                to_agent_id=to_agent_id,
                session_id=session_id,
                handoff_type=handoff_type,
                include_tags=include_tags,
            )
        finally:
            self._session_locks[session_id].release()

    async def _prepare_handoff_internal(
        self,
        from_agent_id: str,
        to_agent_id: str,
        session_id: str,
        handoff_type: str = "summary",
        include_tags: Optional[list[str]] = None,
    ) -> HandoffResult:
        """Prepare context for handoff between agents."""
        # Gather memories for the session
        memories = await self.storage.query_memories(
            session_id=session_id,
            agent_id=from_agent_id,
            tags=include_tags,
        )

        if not memories:
            return HandoffResult(
                success=False,
                summary=f"No memories found for agent '{from_agent_id}' in session '{session_id}'",
                context={},
                warnings=["No memories to hand off"],
            )

        # Build context from memories
        context: dict[str, Any] = {}
        for mem in memories:
            context[mem.key] = mem.value

        # Create handoff payload
        payload = HandoffPayload(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            session_id=session_id,
            context=context,
            handoff_type=handoff_type,
            include_tags=include_tags or [],
        )

        # Run guardrails
        warnings = HandoffGuardrails.validate_payload(payload)
        sanitized = HandoffGuardrails.sanitize_context(payload.context)

        summary = (
            f"Handoff from '{from_agent_id}' to '{to_agent_id}': "
            f"{len(sanitized)} context keys, type={handoff_type}"
        )

        return HandoffResult(
            success=len(warnings) == 0
            or all("sensitive" in w or "identical" in w for w in warnings),
            summary=summary,
            context=sanitized,
            warnings=warnings,
        )

    async def execute_handoff(
        self,
        from_agent_id: str,
        to_agent_id: str,
        session_id: str,
        handoff_type: str = "summary",
        include_tags: Optional[list[str]] = None,
        new_session_id: Optional[str] = None,
    ) -> HandoffResult:
        """Execute a handoff: prepare context, store it for the receiving agent."""
        await self._acquire_session_lock(session_id)
        try:
            result = await self._prepare_handoff_internal(
                from_agent_id=from_agent_id,
                to_agent_id=to_agent_id,
                session_id=session_id,
                handoff_type=handoff_type,
                include_tags=include_tags,
            )

            if not result.success and not result.context:
                return result

            # Store context for the receiving agent
            target_session = new_session_id or session_id
            for key, value in result.context.items():
                entry = MemoryEntry(
                    session_id=target_session,
                    agent_id=to_agent_id,
                    key=f"handoff:{key}",
                    value=value,
                    tags=["handoff", f"from:{from_agent_id}"],
                )
                await self.storage.store_memory(entry)

            result.summary += f" | Stored {len(result.context)} keys for '{to_agent_id}'"
            return result
        finally:
            self._session_locks[session_id].release()
