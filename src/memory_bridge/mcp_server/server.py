"""Memory Bridge MCP Server — core implementation.

Implements the Model Context Protocol (MCP) over stdio as a JSON-RPC 2.0
server. Exposes Memory Bridge operations as composable tools that any
MCP-compatible agent (Claude Code, Cursor, AutoGen, etc.) can discover
and invoke.

Protocol: https://spec.modelcontextprotocol.io
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

logger = logging.getLogger("memory-bridge-mcp")

# ── Tool Definitions ──────────────────────────────────────────────────────

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "store_memory",
        "description": (
            "Store a memory in the shared workspace. Use this when an agent "
            "discovers a fact, learns a user preference, completes a task, or "
            "generates data that other agents may need later."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Unique key for this memory (e.g. 'user_preferences', 'project_deadline', 'deploy_config')",
                },
                "value": {
                    "description": "The memory content — any JSON-serializable value (string, object, array)",
                },
                "agent_id": {
                    "type": "string",
                    "description": "ID of the agent storing this memory",
                },
                "session_id": {
                    "type": "string",
                    "description": "Current session or conversation ID",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for categorization (e.g. ['config', 'user_fact', 'log'])",
                },
            },
            "required": ["key", "value", "agent_id", "session_id"],
        },
    },
    {
        "name": "search_memories",
        "description": (
            "Search stored memories by key or tag. Returns matching memories "
            "with their values, agent_id, session_id, and creation time. "
            "Use this when you need to recall what another agent learned or "
            "find a specific piece of shared knowledge."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Search by exact key or key prefix (e.g. 'user_preferences' matches 'user_preferences' and 'user_preferences_dark_mode')",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by tags (memory must match ALL specified tags)",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Filter by the agent that stored the memory",
                },
                "session_id": {
                    "type": "string",
                    "description": "Filter by session ID",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of memories to return (default: 20, max: 100)",
                    "default": 20,
                },
            },
        },
    },
    {
        "name": "get_memory",
        "description": (
            "Get the full details of a single memory by its ID. "
            "Returns the complete value, metadata, tags, and timestamps."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "The memory's unique ID (UUID)",
                },
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "handoff_memories",
        "description": (
            "Get all memories relevant to handing off context between agents. "
            "Returns memories grouped by session, showing what each agent has "
            "learned. Use this when starting a new task or when a different "
            "agent needs to pick up where another left off."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Filter to only memories stored by this agent",
                },
                "session_id": {
                    "type": "string",
                    "description": "Filter to only memories from this session",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum memories to return (default: 50)",
                    "default": 50,
                },
            },
        },
    },
    {
        "name": "list_sessions",
        "description": (
            "List all active sessions in the shared workspace. "
            "Sessions group related memories together — use this to discover "
            "what contexts exist before searching for specific memories."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum sessions to return (default: 50)",
                    "default": 50,
                },
            },
        },
    },
    {
        "name": "delete_memory",
        "description": (
            "Delete a specific memory by its ID. Use this when a memory is "
            "stale, incorrect, or no longer relevant."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "The memory's unique ID (UUID) to delete",
                },
            },
            "required": ["memory_id"],
        },
    },
]


# ── MCP Protocol Implementation ──────────────────────────────────────────


class MCPServer:
    """Raw JSON-RPC 2.0 MCP server over stdio.

    No SDK dependency — implements the MCP wire protocol directly.
    """

    def __init__(self, api_url: str, api_key: str | None):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key or os.environ.get("MEMORY_BRIDGE_API_KEY", "")
        self._http = httpx.Client(timeout=30.0)
        self._request_id = 0
        self._initialized = False

        # Tool name → handler mapping
        self._handlers: dict[str, Callable] = {
            "store_memory": self._handle_store_memory,
            "search_memories": self._handle_search_memories,
            "get_memory": self._handle_get_memory,
            "handoff_memories": self._handle_handoff_memories,
            "list_sessions": self._handle_list_sessions,
            "delete_memory": self._handle_delete_memory,
        }

    # ── Auth helpers ──────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _api_call(self, method: str, path: str, **kwargs) -> Any:
        """Make an HTTP call to the Memory Bridge API."""
        url = f"{self.api_url}{path}"
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        if method.upper() == "GET":
            resp = self._http.get(url, headers=headers, **kwargs)
        elif method.upper() == "POST":
            resp = self._http.post(url, headers=headers, **kwargs)
        elif method.upper() == "DELETE":
            resp = self._http.delete(url, headers=headers, **kwargs)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        if resp.status_code >= 400:
            detail = resp.text[:500]
            raise RuntimeError(f"API error {resp.status_code}: {detail}")
        return resp.json()

    # ── Tool Handlers ─────────────────────────────────────────────────

    def _handle_store_memory(self, args: dict) -> dict:
        body = {
            "key": args["key"],
            "value": args["value"],
            "agent_id": args["agent_id"],
            "session_id": args["session_id"],
            "tags": args.get("tags", []),
        }
        result = self._api_call("POST", "/memories/", json=body)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "status": "stored",
                        "memory_id": result.get("id", "unknown"),
                        "key": args["key"],
                    }, indent=2),
                }
            ],
        }

    def _handle_search_memories(self, args: dict) -> dict:
        params = {}
        if args.get("key"):
            params["key"] = args["key"]
        if args.get("tags"):
            params["tags"] = ",".join(args["tags"])
        if args.get("agent_id"):
            params["agent_id"] = args["agent_id"]
        if args.get("session_id"):
            params["session_id"] = args["session_id"]
        params["limit"] = str(min(args.get("limit", 20), 100))

        result = self._api_call("GET", "/memories/", params=params)
        memories = result if isinstance(result, list) else result.get("results", result.get("memories", []))

        if not memories:
            return {
                "content": [{"type": "text", "text": "No memories found matching your criteria."}],
            }

        lines = [f"Found {len(memories)} memory(ies):", ""]
        for m in memories:
            created = m.get("created_at", "")[:19] if m.get("created_at") else ""
            tags_str = f"  tags: {', '.join(m.get('tags', []))}" if m.get("tags") else ""
            val = m.get("value", "")
            val_str = json.dumps(val, indent=2) if not isinstance(val, str) else val
            lines.append(f"  [{m.get('id', '')[:8]}…] key={m.get('key', '')}")
            lines.append(f"        agent={m.get('agent_id', '')}  session={m.get('session_id', '')}")
            if created:
                lines.append(f"        created: {created}")
            if tags_str:
                lines.append(tags_str)
            lines.append(f"        value: {val_str[:300]}")
            lines.append("")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    def _handle_get_memory(self, args: dict) -> dict:
        mid = args["memory_id"]
        result = self._api_call("GET", f"/memories/{mid}")
        m = result
        val = m.get("value", "")
        val_str = json.dumps(val, indent=2) if not isinstance(val, str) else str(val)
        tags_str = ", ".join(m.get("tags", [])) if m.get("tags") else "—"
        text = (
            f"Memory: {m.get('id', '')}\n"
            f"Key:    {m.get('key', '')}\n"
            f"Agent:  {m.get('agent_id', '')}\n"
            f"Session:{m.get('session_id', '')}\n"
            f"Tags:   {tags_str}\n"
            f"Created:{m.get('created_at', '')}\n"
            f"Updated:{m.get('updated_at', '')}\n"
            f"Project:{m.get('project', '—')}\n"
            f"\nValue:\n{val_str[:2000]}"
        )
        return {"content": [{"type": "text", "text": text}]}

    def _handle_handoff_memories(self, args: dict) -> dict:
        params = {"limit": str(min(args.get("limit", 50), 200))}
        if args.get("agent_id"):
            params["agent_id"] = args["agent_id"]
        if args.get("session_id"):
            params["session_id"] = args["session_id"]

        result = self._api_call("GET", "/memories/", params=params)
        memories = result if isinstance(result, list) else result.get("results", result.get("memories", []))

        if not memories:
            return {
                "content": [{"type": "text", "text": "No handoff context found — no shared memories exist for this scope."}],
            }

        # Group by session for handoff clarity
        by_session: dict[str, list[dict]] = {}
        for m in memories:
            sid = m.get("session_id", "_unknown")
            by_session.setdefault(sid, []).append(m)

        lines = [f"Handoff context: {len(memories)} memories across {len(by_session)} session(s)", ""]
        for sid, mems in sorted(by_session.items()):
            agents = set(m.get("agent_id", "") for m in mems)
            lines.append(f"  Session: {sid}")
            lines.append(f"  Agents:  {', '.join(sorted(agents))}")
            for m in mems:
                val = m.get("value", "")
                val_str = json.dumps(val, indent=2) if not isinstance(val, str) else str(val)
                lines.append(f"    • [{m.get('key', '')}] = {val_str[:200]}")
            lines.append("")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    def _handle_list_sessions(self, args: dict) -> dict:
        params = {"limit": str(min(args.get("limit", 50), 200))}
        # Query memories and extract unique sessions
        result = self._api_call("GET", "/memories/", params=params)
        memories = result if isinstance(result, list) else result.get("results", result.get("memories", []))

        seen: dict[str, dict] = {}
        for m in memories:
            sid = m.get("session_id")
            if sid and sid not in seen:
                seen[sid] = {
                    "session_id": sid,
                    "agent_ids": set(),
                    "memory_count": 0,
                    "tags": set(),
                    "created_at": m.get("created_at", ""),
                }
            if sid and sid in seen:
                seen[sid]["agent_ids"].add(m.get("agent_id", ""))
                seen[sid]["memory_count"] += 1
                for t in m.get("tags", []):
                    seen[sid]["tags"].add(t)

        if not seen:
            return {"content": [{"type": "text", "text": "No sessions found."}]}

        lines = [f"Active sessions ({len(seen)}):", ""]
        for sid, info in sorted(seen.items()):
            agents = ", ".join(sorted(info["agent_ids"]))
            tags = ", ".join(sorted(info["tags"]))[:100]
            created = info["created_at"][:19] if info["created_at"] else ""
            lines.append(f"  {sid}")
            lines.append(f"    agents: {agents}")
            lines.append(f"    memories: {info['memory_count']}")
            if tags:
                lines.append(f"    tags: {tags}")
            if created:
                lines.append(f"    since: {created}")
            lines.append("")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    def _handle_delete_memory(self, args: dict) -> dict:
        mid = args["memory_id"]
        self._api_call("DELETE", f"/memories/{mid}")
        return {
            "content": [{"type": "text", "text": f"Memory {mid} deleted successfully."}],
        }

    # ── JSON-RPC Message Handling ─────────────────────────────────────

    def _send(self, msg: dict) -> None:
        line = json.dumps(msg, default=str)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def _send_error(self, req_id: Any, code: int, message: str, data: Any = None) -> None:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        self._send({"jsonrpc": "2.0", "id": req_id, "error": err})

    def _send_result(self, req_id: Any, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _handle_message(self, msg: dict) -> None:
        method = msg.get("method", "")
        req_id = msg.get("id")
        params = msg.get("params", {}) or {}

        # ── Lifecycle ──
        if method == "initialize":
            self._send_result(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                    "resources": {},
                },
                "serverInfo": {
                    "name": "memory-bridge-mcp",
                    "version": "0.1.0",
                },
            })
            return

        if method == "notifications/initialized":
            self._initialized = True
            return

        if method == "notifications/cancelled":
            return  # Ignore cancellations

        # ── Tools ──
        if method == "tools/list":
            self._send_result(req_id, {"tools": TOOL_DEFINITIONS})
            return

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            handler = self._handlers.get(tool_name)
            if not handler:
                self._send_error(req_id, -32601, f"Unknown tool: {tool_name}")
                return
            try:
                result = handler(arguments)
                self._send_result(req_id, result)
            except Exception as e:
                logger.exception("Tool call failed: %s", tool_name)
                self._send_error(req_id, -32603, str(e), {"tool": tool_name})
            return

        # ── Resources (not implemented — memory is accessed via tools) ──
        if method == "resources/list":
            self._send_result(req_id, {"resources": []})
            return

        if method == "resources/read":
            self._send_error(req_id, -32601, "Resources not supported — use tools instead")
            return

        # Unknown method
        if req_id is not None:
            self._send_error(req_id, -32601, f"Method not found: {method}")

    # ── Main Loop ─────────────────────────────────────────────────────

    def run(self) -> None:
        """Read JSON-RPC messages from stdin and dispatch them."""
        logger.info("MCP server starting (api_url=%s)", self.api_url)
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                self._handle_message(msg)
            except json.JSONDecodeError as e:
                logger.warning("Invalid JSON received: %s", e)
                # Can't send error without a valid request ID — skip
            except Exception as e:
                logger.exception("Unhandled error processing message")
                # Try to send a generic error if possible
                try:
                    self._send_error(None, -32603, str(e))
                except Exception:
                    pass


def main():
    """Entry point for the MCP server CLI."""
    import argparse

    parser = argparse.ArgumentParser(description="Memory Bridge MCP Server")
    parser.add_argument("--api-url", default=os.environ.get("MEMORY_BRIDGE_API_URL", "http://localhost:8000"))
    parser.add_argument("--api-key", default=os.environ.get("MEMORY_BRIDGE_API_KEY", ""))
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(levelname)s %(name)s: %(message)s",
    )

    server = MCPServer(api_url=args.api_url, api_key=args.api_key)
    try:
        server.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
