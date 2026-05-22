# Memory Bridge Roadmap

> *Where we've been, where we're going, and how you can help us get there.*

**Legend:** 🚀 Shipped · 🔨 Building · 📋 Planned · 💭 Dreaming

---

## v0.1 — MVP 🎉 **🚀 SHIPPED**

The foundation. A working cross-session memory system for multi-agent teams.

- [x] FastAPI REST server with `/memories`, `/sessions` CRUD
- [x] Async SQLite storage with session/memory tables
- [x] Agent-to-agent handoff protocol with guardrails
  - Sensitive key blocking (`api_key`, `token`, `password`, `secret`)
  - Context size limits (100K characters)
  - Tag-based selective handoff
- [x] Session chaining (parent-child relationships)
- [x] 37 unit tests + 19 end-to-end smoke tests
- [x] GitHub Actions CI (Python 3.9–3.12)
- [x] Docker multi-stage build
- [x] CLI entry point (`memory-bridge`)

---

## v0.2 — Production Hardening 🛡️ **🔨 BUILDING**

Making Memory Bridge safe to run in production.

- [x] Modern FastAPI lifespan pattern (replaces deprecated `on_event`)
- [x] Production Dockerfile without `reload=True`
- [ ] API key authentication middleware
- [ ] Rate limiting (in-memory, Redis-backed via env var)
- [ ] TTL / eviction policy for memories
- [ ] Health dashboard endpoint (`/health` with metrics)
- [ ] `bridge migrate` CLI for importing existing session data

---

## v0.3 — Performance & Scale 📈 **📋 PLANNED**

Making Memory Bridge fast at scale.

- [ ] Tag junction table (replaces client-side O(n) filtering)
- [ ] PostgreSQL storage backend
- [ ] Connection pooling for multi-worker deployments
- [ ] Warning severity enum (replaces fragile string matching)
- [ ] Cascading session deletes
- [ ] Pagination on query endpoints
- [ ] Benchmarks and performance testing

---

## v0.4 — Framework Integration 🔗 **📋 PLANNED**

Making Memory Bridge work with the tools people actually use.

- [ ] LangGraph adapter (handoff envelope schema)
- [ ] AutoGen integration
- [ ] CrewAI integration
- [ ] OpenAI Assistants API integration
- [ ] WebSocket streaming for real-time memory sync
- [ ] Example notebooks and tutorials

---

## v0.5 — Nova's Dreams ✨ **💭 DREAMING**

The visionary features that make agents feel alive.

- [ ] **Agent lineages** — child agents inherit parent memory context
- [ ] **Memory weights** — frequently accessed memories persist, noise decays
- [ ] **Cross-team bridges** — secure context sharing between agent teams
- [ ] **Graph-based memory navigation** — traverse related memories
- [ ] **Semantic memory search** — vector embeddings for similarity retrieval

---

## How to Contribute

Check out [CONTRIBUTING.md](./CONTRIBUTING.md) to see how you can help — whether you're a dreamer, a critic, an architect, or a builder. Every role matters.
