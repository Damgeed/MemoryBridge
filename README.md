# Memory Bridge

[![CI](https://github.com/Damgeed/MemoryBridge/actions/workflows/ci.yml/badge.svg)](https://github.com/Damgeed/MemoryBridge/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%20|%203.10%20|%203.11%20|%203.12-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker)](https://hub.docker.com/)

**Cross-session memory persistence for multi-agent AI teams.**

Memory Bridge is a middleware layer that lets AI agents share context across sessions. It provides:

- **Session Persistence** — Store and retrieve agent context across sessions
- **Memory Tagging** — Organize memories with tags for selective retrieval
- **Agent Handoff** — Pass context between agents with guardrails
- **Pluggable Storage** — SQLite out of the box, upgrade to PostgreSQL/Redis later

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
