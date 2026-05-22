"""Memory Bridge — Python client SDK for agent integration.

Async client wrapping all Memory Bridge API endpoints.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx


class MemoryBridgeError(Exception):
    """Base exception for Memory Bridge client errors."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"[{status_code}] {detail}")


class Client:
    """Async client for the Memory Bridge API.

    Usage::

        async with Client(base_url="http://localhost:8000", api_key="...") as client:
            health = await client.health()
            mem = await client.create_memory(
                session_id="s1", agent_id="a1",
                key="user_name", value="Alice",
            )
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url, headers=headers, timeout=timeout,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_response(self, resp: httpx.Response) -> httpx.Response:
        """Raise MemoryBridgeError on non-2xx responses."""
        if resp.status_code >= 400:
            detail: str = resp.text
            try:
                body = resp.json()
                if isinstance(body, dict):
                    detail = body.get("detail", detail)
            except Exception:
                pass
            raise MemoryBridgeError(resp.status_code, detail)
        return resp

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health(self) -> dict:
        """Retrieve health and metrics from the server."""
        resp = await self._client.get("/health")
        return self._check_response(resp).json()

    # ------------------------------------------------------------------
    # Memory CRUD
    # ------------------------------------------------------------------

    async def create_memory(
        self,
        session_id: str,
        agent_id: str,
        key: str,
        value: Any,
        tags: Optional[list[str]] = None,
        ttl_seconds: Optional[int] = None,
        propagate_to_parent: bool = False,
    ) -> dict:
        """Create a new memory entry."""
        body = {
            "session_id": session_id,
            "agent_id": agent_id,
            "key": key,
            "value": value,
            "tags": tags or [],
            "ttl_seconds": ttl_seconds,
            "propagate_to_parent": propagate_to_parent,
        }
        resp = await self._client.post("/memories", json=body)
        return self._check_response(resp).json()

    async def get_memory(self, memory_id: str) -> dict:
        """Retrieve a single memory entry by its ID."""
        resp = await self._client.get(f"/memories/{memory_id}")
        return self._check_response(resp).json()

    async def query_memories(
        self,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        keys: Optional[list[str]] = None,
        limit: int = 50,
        offset: int = 0,
        include_lineage: bool = False,
    ) -> dict:
        """Query memories with optional filters.

        Returns ``{"entries": [...], "total": int}``.
        """
        body = {
            "session_id": session_id,
            "agent_id": agent_id,
            "tags": tags or [],
            "keys": keys or [],
            "limit": limit,
            "offset": offset,
        }
        params = "?include_lineage=true" if include_lineage else ""
        resp = await self._client.post(f"/memories/query{params}", json=body)
        return self._check_response(resp).json()

    async def search_memories(
        self,
        q: str,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Full-text search across memories.

        Returns ``{"entries": [...], "total": int}``.
        """
        params: dict[str, Any] = {"q": q, "limit": limit, "offset": offset}
        if session_id is not None:
            params["session_id"] = session_id
        if agent_id is not None:
            params["agent_id"] = agent_id
        resp = await self._client.get("/memories/search", params=params)
        return self._check_response(resp).json()

    async def delete_memory(self, memory_id: str) -> dict:
        """Delete a memory entry by ID."""
        resp = await self._client.delete(f"/memories/{memory_id}")
        return self._check_response(resp).json()

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    async def create_session(
        self,
        session_id: str,
        agent_id: str,
        parent_session_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Create or register a new agent session."""
        body = {
            "session_id": session_id,
            "agent_id": agent_id,
            "parent_session_id": parent_session_id,
            "metadata": metadata or {},
        }
        resp = await self._client.post("/sessions", json=body)
        return self._check_response(resp).json()

    async def get_session(self, session_id: str) -> dict:
        """Retrieve a session by its ID."""
        resp = await self._client.get(f"/sessions/{session_id}")
        return self._check_response(resp).json()

    # ------------------------------------------------------------------
    # Handoff Protocol
    # ------------------------------------------------------------------

    async def handoff_prepare(
        self,
        from_agent_id: str,
        to_agent_id: str,
        session_id: str,
        context: dict,
        handoff_type: str = "summary",
        include_tags: Optional[list[str]] = None,
    ) -> dict:
        """Prepare context for an agent-to-agent handoff.

        Returns handoff summary and context without modifying state.
        """
        body = {
            "from_agent_id": from_agent_id,
            "to_agent_id": to_agent_id,
            "session_id": session_id,
            "context": context,
            "handoff_type": handoff_type,
            "include_tags": include_tags or [],
        }
        resp = await self._client.post("/handoff/prepare", json=body)
        return self._check_response(resp).json()

    async def handoff_execute(
        self,
        from_agent_id: str,
        to_agent_id: str,
        session_id: str,
        context: dict,
        handoff_type: str = "summary",
        include_tags: Optional[list[str]] = None,
    ) -> dict:
        """Execute an agent-to-agent handoff.

        Prepares context and stores memories for the receiving agent.
        """
        body = {
            "from_agent_id": from_agent_id,
            "to_agent_id": to_agent_id,
            "session_id": session_id,
            "context": context,
            "handoff_type": handoff_type,
            "include_tags": include_tags or [],
        }
        resp = await self._client.post("/handoff/execute", json=body)
        return self._check_response(resp).json()
