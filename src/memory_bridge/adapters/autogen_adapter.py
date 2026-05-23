"""Memory Bridge adapter for AutoGen.

Usage:
    from memory_bridge.adapters.autogen_adapter import MemoryBridgeAgent

    # In your AutoGen app:
    agent = MemoryBridgeAgent(
        name="assistant",
        api_key="mb_...",
        base_url="http://localhost:8000",
    )
    # Agent now has persistent memory via Memory Bridge
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MemoryBridgeAgent:
    """Memory Bridge adapter for AutoGen.

    Wraps an AutoGen ConversableAgent with persistent memory
    via Memory Bridge. All conversation history is stored
    and retrievable across sessions.
    """

    def __init__(
        self,
        name: str,
        api_key: str = "",
        base_url: str = "http://localhost:8000",
        session_id: Optional[str] = None,
        project: Optional[str] = None,
    ):
        self.name = name
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id or f"autogen-{name}"
        self.project = project
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        logger.info("Created MemoryBridgeAgent '%s' (session: %s)", name, self.session_id)

    async def remember(self, key: str, value: Any) -> bool:
        """Store a memory."""
        import httpx
        payload = {
            "session_id": self.session_id,
            "agent_id": self.name,
            "key": key,
            "value": value,
            "tags": ["autogen", self.name],
            "project": self.project,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{self.base_url}/memories", json=payload, headers=self._headers)
            return resp.status_code == 200

    async def recall(self, key: str) -> Optional[Any]:
        """Recall a memory by key."""
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/memories/query",
                json={"session_id": self.session_id, "keys": [key], "project": self.project},
                headers=self._headers,
            )
            if resp.status_code == 200:
                entries = resp.json().get("entries", [])
                if entries:
                    return entries[0].get("value")
        return None

    async def search(self, query: str) -> list[dict]:
        """Search all memories."""
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/memories/search",
                params={"q": query, "session_id": self.session_id},
                headers=self._headers,
            )
            if resp.status_code == 200:
                return resp.json().get("entries", [])
        return []
