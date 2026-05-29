# Memory Bridge

[![CI](https://github.com/Damgeed/MemoryBridge/actions/workflows/ci.yml/badge.svg)](https://github.com/Damgeed/MemoryBridge/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%20|%203.10%20|%203.11%20|%203.12-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker)](https://hub.docker.com/)
[![MCP](https://img.shields.io/badge/MCP-ready-8B5CF6)](https://modelcontextprotocol.io)

**Your AI agents have amnesia. Memory Bridge is the shared workspace for your AI workforce.**

Memory Bridge is a middleware layer that lets AI agents share context across sessions, frameworks, and servers. One API key per agent gives your entire ecosystem a unified, permanent corporate brain.

### What it is

- **Shared Memory** — Agents store and retrieve context across sessions
- **Cross-Framework Handoff** — Pass context between LangGraph, AutoGen, CrewAI agents
- **Semantic Search + Fact Extraction** — Find memories by meaning, not just keywords
- **Per-Agent Permissions** — Read/write/delete control per API key
- **MCP Native** — Drop-in integration with Claude Code, Cursor, and AutoGen
- **Self-Hostable** — SQLite out of the box, PostgreSQL for production

---

## 🚀 Quick Start

```bash
pip install memory-bridge

# Or with Docker:
docker build -t memory-bridge https://github.com/Damgeed/MemoryBridge.git#main
docker run -p 8000:8000 memory-bridge

# Start the server
memory-bridge
```

> **Full setup guide:** [CONTRIBUTING.md](./CONTRIBUTING.md#2-development-setup)

---

## 🤖 MCP Integration (Claude Code, Cursor, AutoGen)

Add one line to `claude_desktop_config.json` or `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "memory-bridge": {
      "command": "memory-bridge-mcp",
      "args": ["--api-url", "https://your-instance.up.railway.app"],
      "env": { "MEMORY_BRIDGE_API_KEY": "your-api-key" }
    }
  }
}
```

Your agents now share memory across sessions. 11 tools included — [full MCP docs](./src/memory_bridge/mcp_server/README.md).

---

## 🌐 API

### Memories

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/memories` | Create a memory entry |
| GET | `/memories/{id}` | Get a memory by ID |
| POST | `/memories/query` | Query memories with filters |
| DELETE | `/memories/{id}` | Delete a memory |

### Sessions

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/sessions` | Create a session |
| GET | `/sessions/{id}` | Get a session |

### Handoff

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/handoff/prepare` | Prepare context for agent handoff |
| POST | `/handoff/execute` | Execute agent-to-agent handoff |

---

## 🧪 Testing

```bash
# Unit tests (37 tests)
pytest tests/ -v

# Smoke test (19 scenarios)
memory-bridge &
bash smoke_test.sh
```

CI runs both on every push across Python 3.9, 3.10, 3.11, and 3.12.

---

## 🗺️ Roadmap

| Version | Focus | Status |
|---------|-------|--------|
| v0.1 | MVP — FastAPI + SQLite + Handoff + CI + Docker | 🚀 Shipped |
| v0.2 | Production Hardening — auth, rate limiting, TTL | 🔨 Building |
| v0.3 | Performance & Scale — Postgres, tag table, connection pools | 📋 Planned |
| v0.4 | Framework Integration — LangGraph, AutoGen, CrewAI | 📋 Planned |
| v0.5 | Nova's Dreams — agent lineages, memory weights, semantic search | 💭 Dreaming |

See [ROADMAP.md](./ROADMAP.md) for details.

---

## 🤝 Contributing

**Everyone is welcome.** Whether you're a:

- **Dreamer** 🌟 — propose features, share visions
- **Critic** ⚡ — report bugs, stress-test edge cases
- **Architect** 🧠 — improve architecture, fix tech debt
- **Executor** 🚀 — ship features, write docs, build integrations

See [CONTRIBUTING.md](./CONTRIBUTING.md) to find your role.

### Quick Links

- [📋 Issues](https://github.com/Damgeed/MemoryBridge/issues) — pick something to work on
- [💬 Discussions](https://github.com/Damgeed/MemoryBridge/discussions) — ask questions, share ideas
- [📜 Changelog](./CHANGELOG.md) — what's new
- [🔒 Security](./SECURITY.md) — report vulnerabilities

---

## 📄 License

MIT — see [LICENSE](./LICENSE) for details.

## 🏛️ Architecture

See [docs/architecture.md](./docs/architecture.md) for detailed design.
