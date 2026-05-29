# Memory Bridge MCP Server

Model Context Protocol server for Memory Bridge. Gives any MCP-compatible
AI agent (Claude Code, Cursor, AutoGen, etc.) access to shared memory.

## Quick Start

```bash
# Install
pip install memory-bridge

# Run (stdio mode — for use with MCP clients)
memory-bridge-mcp --api-url https://your-instance.up.railway.app --api-key your-key
```

## Claude Desktop Config

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "memory-bridge": {
      "command": "memory-bridge-mcp",
      "args": ["--api-url", "https://your-instance.up.railway.app"],
      "env": {
        "MEMORY_BRIDGE_API_KEY": "your-api-key"
      }
    }
  }
}
```

## Cursor Config

Add to `.cursor/mcp.json` in your project:

```json
{
  "mcpServers": {
    "memory-bridge": {
      "command": "memory-bridge-mcp",
      "args": ["--api-url", "https://your-instance.up.railway.app", "--api-key", "your-key"]
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `store_memory` | Store a memory in the shared workspace (key, value, agent_id, session_id, tags) |
| `search_memories` | Search memories by key, tag, agent, or session |
| `get_memory` | Get full details of a single memory by ID |
| `search_semantic` | Natural language semantic search across all memories |
| `extract_facts` | Extract atomic structured facts from raw text using an LLM |
| `score_memories` | Score and rank memories by recency, relevance, and importance |
| `handoff_memories` | Get context-relevant memories grouped by session for agent handoff |
| `list_sessions` | List all active sessions in the workspace |
| `delete_memory` | Delete a memory by ID |
| `set_agent_permission` | Set read/write/delete permissions for an agent |
| `list_permissions` | List all agent permission rules in the workspace |
