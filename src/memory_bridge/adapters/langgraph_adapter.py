"""Memory Bridge adapter for LangGraph.

Usage:
    from memory_bridge.adapters.langgraph_adapter import MemoryBridgeSaver

    # In your LangGraph app:
    checkpointer = MemoryBridgeSaver(
        api_key="mb_...",
        base_url="http://localhost:8000",
        project="my-project",
    )
    graph = StateGraph(AgentState)
    app = graph.compile(checkpointer=checkpointer)
"""

import json
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class MemoryBridgeSaver:
    """LangGraph BaseCheckpointSaver-compatible adapter.

    Stores LangGraph execution state (checkpoints) in Memory Bridge
    so agents remember state across sessions.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "http://localhost:8000",
        project: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.project = project
        self.session_id = session_id or "langgraph-default"
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def put(self, config: dict, checkpoint: dict, metadata: Optional[dict] = None) -> None:
        """Store a checkpoint (LangGraph state)."""
        key = f"checkpoint:{config.get('configurable', {}).get('thread_id', 'default')}"
        payload = {
            "session_id": self.session_id,
            "agent_id": "langgraph",
            "key": key,
            "value": {
                "config": config,
                "checkpoint": checkpoint,
                "metadata": metadata or {},
            },
            "tags": ["langgraph", "checkpoint"],
            "project": self.project,
        }
        async with httpx.AsyncClient() as client:
            await client.post(self._url("/memories"), json=payload, headers=self._headers)

    async def get(self, config: dict) -> Optional[dict]:
        """Retrieve a checkpoint."""
        thread_id = config.get("configurable", {}).get("thread_id", "default")
        key = f"checkpoint:{thread_id}"
        params = {"session_id": self.session_id}
        if self.project:
            params["project"] = self.project
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                self._url(f"/memories/search?q={key}"),
                params=params,
                headers=self._headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                entries = data.get("entries", [])
                if entries:
                    return entries[0].get("value", {})
        return None

    async def list(self, config: dict, **kwargs) -> list[dict]:
        """List all checkpoints for a thread."""
        thread_id = config.get("configurable", {}).get("thread_id", "default")
        key = f"checkpoint:{thread_id}"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self._url("/memories/query"),
                json={"session_id": self.session_id, "keys": [key], "project": self.project},
                headers=self._headers,
            )
            if resp.status_code == 200:
                return [e.get("value", {}) for e in resp.json().get("entries", [])]
        return []
