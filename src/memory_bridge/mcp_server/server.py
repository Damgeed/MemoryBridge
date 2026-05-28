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
            "agent needs to pick up where another left off. "
            "Optionally provide a 'query' for scored/ranked results by relevance."
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
                "query": {
                    "type": "string",
                    "description": "Optional natural language query for relevance-based ranking",
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
    {
        "name": "search_semantic",
        "description": (
            "Natural language semantic search across all memories. "
            "Returns relevant memories sorted by relevance score, "
            "using the configured embedding model for vector similarity. "
            "Use this instead of search_memories when you have a natural "
            "language query and want meaning-based matching rather than "
            "exact key/tag filtering."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 10, max: 50)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "extract_facts",
        "description": (
            "Extract atomic, structured facts from raw text using an LLM. "
            "Returns facts with categories (preference, fact, decision, log, other), "
            "confidence scores, and extracted entities. Optionally stores each "
            "fact as a separate memory entry for later retrieval."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Raw text to extract facts from",
                },
                "store_facts": {
                    "type": "boolean",
                    "description": "If true, store each extracted fact as a separate memory entry",
                    "default": False,
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID (required when store_facts=true)",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session ID (required when store_facts=true)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags to apply when storing facts",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "score_memories",
        "description": (
            "Score and rank memories by recency, relevance, and importance. "
            "Returns each memory with a composite score (0-1) and its component "
            "scores. Provide a 'query' for relevance-based ranking, or omit it "
            "for recency+importance-only ranking."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query for relevance scoring (optional)",
                },
                "session_id": {
                    "type": "string",
                    "description": "Filter by session ID",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Filter by agent ID",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of memories to return (default: 20, max: 200)",
                    "default": 20,
                },
                "weights": {
                    "type": "object",
                    "description": "Custom scoring weights: {\"recency\": 0.3, \"relevance\": 0.5, \"importance\": 0.2}",
                    "properties": {
                        "recency": {"type": "number"},
                        "relevance": {"type": "number"},
                        "importance": {"type": "number"},
                    },
                },
            },
        },
    },
    {
        "name": "set_agent_permission",
        "description": (
            "Set or update permissions for an agent in the shared workspace. "
            "Use this to control which agents can read, write, or delete "
            "memories. When no permission rule exists, agents have full access "
            "(backward compatible)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "ID of the agent to set permissions for",
                },
                "can_read": {
                    "type": "boolean",
                    "description": "Allow agent to read memories stored by other agents (default: true)",
                },
                "can_write": {
                    "type": "boolean",
                    "description": "Allow agent to store new memories (default: true)",
                },
                "can_delete": {
                    "type": "boolean",
                    "description": "Allow agent to delete memories (default: false)",
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "list_permissions",
        "description": (
            "List all agent permission rules currently configured in the "
            "shared workspace. Returns the list of permissions with agent IDs, "
            "read/write/delete flags, and timestamps."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
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
            "search_semantic": self._handle_search_semantic,
            "extract_facts": self._handle_extract_facts,
            "score_memories": self._handle_score_memories,
            "set_agent_permission": self._handle_set_agent_permission,
            "list_permissions": self._handle_list_permissions,
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
        elif method.upper() == "PUT":
            resp = self._http.put(url, headers=headers, **kwargs)
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

        query = args.get("query", "")

        if query:
            # Use scoring endpoint for relevance-based ranking
            body = {
                "query": query,
                "limit": min(args.get("limit", 50), 200),
            }
            if args.get("agent_id"):
                body["agent_id"] = args["agent_id"]
            if args.get("session_id"):
                body["session_id"] = args["session_id"]
            result = self._api_call("POST", "/memories/score", json=body)
            scored_results = result.get("results", []) if isinstance(result, dict) else []
            memories = [r.get("memory", r) for r in scored_results]
            scores = {r.get("memory", {}).get("id", ""): r for r in scored_results}
        else:
            # No query — fetch raw and sort by recency
            result = self._api_call("GET", "/memories/", params=params)
            memories = result if isinstance(result, list) else result.get("results", result.get("memories", []))
            # Sort by created_at descending (most recent first)
            memories.sort(key=lambda m: m.get("created_at", "") or "", reverse=True)
            scores = {}

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
        if query:
            lines.append(f"  Ranked by relevance to: \"{query}\"")
            lines.append("")
        for sid, mems in sorted(by_session.items()):
            agents = set(m.get("agent_id", "") for m in mems)
            lines.append(f"  Session: {sid}")
            lines.append(f"  Agents:  {', '.join(sorted(agents))}")
            for m in mems:
                mid = m.get("id", "")
                score_info = ""
                if mid in scores:
                    s = scores[mid]
                    score_info = "  [score={:.4f} r={:.2f} v={:.2f} i={:.2f}]".format(
                        s.get("score", 0),
                        s.get("recency_score", 0),
                        s.get("relevance_score", 0),
                        s.get("importance_score", 0),
                    )
                val = m.get("value", "")
                val_str = json.dumps(val, indent=2) if not isinstance(val, str) else str(val)
                lines.append("    • [{key}]{score} = {val}".format(
                    key=m.get("key", ""),
                    score=score_info,
                    val=val_str[:200],
                ))
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

    def _handle_search_semantic(self, args: dict) -> dict:
        """Natural language semantic search."""
        query = args["query"]
        limit = min(args.get("limit", 10), 50)

        params = {
            "q": query,
            "limit": str(limit),
        }
        result = self._api_call("GET", "/memories/search", params=params)

        results = result.get("results", []) if isinstance(result, dict) else []
        provider = result.get("provider", "unknown") if isinstance(result, dict) else "unknown"

        if not results:
            return {
                "content": [{"type": "text", "text": "No relevant memories found."}],
            }

        lines = [
            f"Semantic search results ({provider}):",
            f"Found {len(results)} relevant memory(ies):",
            "",
        ]
        for r in results:
            m = r.get("memory", {})
            score = r.get("score", 0)
            matched_by = r.get("matched_by", "unknown")
            created = m.get("created_at", "")[:19] if m.get("created_at") else ""
            tags_str = (
                f"  tags: {', '.join(m.get('tags', []))}"
                if m.get("tags") else ""
            )
            val = m.get("value", "")
            val_str = json.dumps(val, indent=2) if not isinstance(val, str) else val
            lines.append(f"  [{m.get('id', '')[:8]}…] score={score:.4f} ({matched_by})")
            lines.append(f"        key={m.get('key', '')}  agent={m.get('agent_id', '')}")
            if created:
                lines.append(f"        created: {created}")
            if tags_str:
                lines.append(tags_str)
            lines.append(f"        value: {val_str[:300]}")
            lines.append("")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    def _handle_extract_facts(self, args: dict) -> dict:
        """Extract facts from text using the API."""

        body = {
            "text": args["text"],
            "source_key": args.get("source_key", ""),
            "store_facts": args.get("store_facts", False),
            "max_facts": min(args.get("max_facts", 10), 25),
        }
        if args.get("agent_id"):
            body["agent_id"] = args["agent_id"]
        if args.get("session_id"):
            body["session_id"] = args["session_id"]
        if args.get("tags"):
            body["tags"] = args["tags"]

        try:
            result = self._api_call("POST", "/memories/extract", json=body)
        except RuntimeError as e:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Fact extraction failed: {}".format(str(e)),
                    }
                ],
            }

        facts = result.get("facts", [])
        provider = result.get("provider", "unknown")
        stored_count = result.get("stored_count", 0)

        if not facts:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "No facts could be extracted from the provided text.",
                    }
                ],
            }

        lines = [
            "Extracted {} fact(s) using {}:".format(len(facts), provider),
            "",
        ]
        for i, f in enumerate(facts):
            entities = ", ".join(f.get("entities", []))
            entities_str = " [entities: {}]".format(entities) if entities else ""
            lines.append(
                "  {}. [{}] (confidence: {:.2f}){}{}".format(
                    i + 1,
                    f.get("category", "other"),
                    f.get("confidence", 0.0),
                    entities_str,
                )
            )
            lines.append("     {}".format(f.get("fact", "")))
            lines.append("")

        if stored_count:
            lines.append("Stored {} fact(s) as memories.".format(stored_count))

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    def _handle_score_memories(self, args: dict) -> dict:
        """Score and rank memories by recency, relevance, and importance."""
        body = {}
        if args.get("query"):
            body["query"] = args["query"]
        if args.get("session_id"):
            body["session_id"] = args["session_id"]
        if args.get("agent_id"):
            body["agent_id"] = args["agent_id"]
        if args.get("weights"):
            body["weights"] = args["weights"]
        body["limit"] = min(args.get("limit", 20), 200)

        result = self._api_call("POST", "/memories/score", json=body)

        results = result.get("results", []) if isinstance(result, dict) else []
        count = result.get("count", 0) if isinstance(result, dict) else 0

        if not results:
            return {
                "content": [{"type": "text", "text": "No memories to score."}],
            }

        lines = [
            f"Scored {count} memories:",
            "",
        ]
        for r in results:
            m = r.get("memory", {})
            score = r.get("score", 0)
            recency = r.get("recency_score", 0)
            relevance = r.get("relevance_score", 0)
            importance = r.get("importance_score", 0)
            created = m.get("created_at", "")[:19] if m.get("created_at") else ""
            tags_str = (
                f"  tags: {', '.join(m.get('tags', []))}"
                if m.get("tags") else ""
            )
            val = m.get("value", "")
            val_str = json.dumps(val, indent=2) if not isinstance(val, str) else val
            lines.append(
                "  [{id}] key={key}".format(
                    id=m.get("id", "")[:8],
                    key=m.get("key", ""),
                )
            )
            lines.append(
                "        score={:.4f}  recency={:.2f}  relevance={:.2f}  importance={:.2f}".format(
                    score, recency, relevance, importance,
                )
            )
            lines.append(
                "        agent={agent}  session={session}".format(
                    agent=m.get("agent_id", ""),
                    session=m.get("session_id", ""),
                )
            )
            if created:
                lines.append("        created: {}".format(created))
            if tags_str:
                lines.append(tags_str)
            lines.append("        value: {}".format(val_str[:200]))
            lines.append("")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    def _handle_set_agent_permission(self, args: dict) -> dict:
        """Set or update agent permissions."""
        agent_id = args["agent_id"]
        body = {}
        if "can_read" in args:
            body["can_read"] = args["can_read"]
        if "can_write" in args:
            body["can_write"] = args["can_write"]
        if "can_delete" in args:
            body["can_delete"] = args["can_delete"]

        result = self._api_call("PUT", f"/permissions/{agent_id}", json=body)
        permission = result.get("permission", {})
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "status": result.get("status", "updated"),
                        "agent_id": agent_id,
                        "can_read": permission.get("can_read", True),
                        "can_write": permission.get("can_write", True),
                        "can_delete": permission.get("can_delete", False),
                    }, indent=2),
                }
            ],
        }

    def _handle_list_permissions(self, args: dict) -> dict:
        """List all agent permission rules."""
        result = self._api_call("GET", "/permissions/")
        permissions = result.get("permissions", [])

        if not permissions:
            return {
                "content": [{"type": "text", "text": "No permission rules configured — all agents have full default access."}],
            }

        lines = [f"Agent Permissions ({len(permissions)}):", ""]
        for p in permissions:
            lines.append(f"  Agent: {p.get('agent_id', '')}")
            project = p.get('project')
            if project:
                lines.append(f"  Project: {project}")
            lines.append(f"    Read:   {p.get('can_read', True)}")
            lines.append(f"    Write:  {p.get('can_write', True)}")
            lines.append(f"    Delete: {p.get('can_delete', False)}")
            lines.append("")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

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
