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
| `store_memory` | Store a memory in the shared workspace |
| `search_memories` | Search memories by key, tag, agent, or session |
| `get_memory` | Get full details of a single memory by ID |
| `handoff_memories` | Get context-relevant memories grouped by session |
| `list_sessions` | List all active sessions in the workspace |
| `delete_memory` | Delete a memory by ID |
