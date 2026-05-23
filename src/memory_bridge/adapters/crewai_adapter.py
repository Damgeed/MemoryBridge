"""Memory Bridge adapter for CrewAI.

Usage:
    from memory_bridge.adapters.crewai_adapter import MemoryBridgeTool

    # In your CrewAI Crew:
    memory_tool = MemoryBridgeTool(api_key="mb_...")
    crew = Crew(agents=[agent], tasks=[task], tools=[memory_tool])
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MemoryBridgeTool:
    """Memory Bridge tool for CrewAI.

    Provides CrewAI agents with persistent memory capabilities
    via Memory Bridge. Can store and retrieve information
    across crew runs.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "http://localhost:8000",
        session_id: Optional[str] = None,
        project: Optional[str] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id or "crewai-default"
        self.project = project
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def _run(self, action: str, **kwargs) -> str:
        """Execute a memory operation.
        action: 'store', 'recall', 'search'
        """
        import httpx
        import asyncio

        async def _async_run():
            async with httpx.AsyncClient() as client:
                if action == "store":
                    payload = {
                        "session_id": self.session_id,
                        "agent_id": "crewai",
                        "key": kwargs.get("key", ""),
                        "value": kwargs.get("value", ""),
                        "tags": ["crewai"],
                        "project": self.project,
                    }
                    resp = await client.post(f"{self.base_url}/memories", json=payload, headers=self._headers)
                    return f"Stored: {resp.status_code}"

                elif action == "recall":
                    resp = await client.post(
                        f"{self.base_url}/memories/query",
                        json={
                            "session_id": self.session_id,
                            "keys": [kwargs.get("key", "")],
                            "project": self.project,
                        },
                        headers=self._headers,
                    )
                    if resp.status_code == 200:
                        entries = resp.json().get("entries", [])
                        if entries:
                            return str(entries[0].get("value", ""))
                    return "Not found"

                elif action == "search":
                    resp = await client.get(
                        f"{self.base_url}/memories/search",
                        params={"q": kwargs.get("query", ""), "session_id": self.session_id},
                        headers=self._headers,
                    )
                    if resp.status_code == 200:
                        return str(resp.json().get("entries", []))
                    return "[]"

                return f"Unknown action: {action}"

        return asyncio.run(_async_run())

    def store(self, key: str, value: Any) -> str:
        return self._run("store", key=key, value=value)

    def recall(self, key: str) -> str:
        return self._run("recall", key=key)

    def search(self, query: str) -> str:
        return self._run("search", query=query)
