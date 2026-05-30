# Memory Bridge — Complete Capability List

## 🧠 Core Memory Engine

| Capability | Description | API |
|-----------|-------------|-----|
| **Persistent key-value memory** | Store and retrieve any JSON-serializable value by key | `POST /memories` / `GET /memories/{id}` |
| **Typed memory** | Three types: `episodic` (what happened), `semantic` (what we know), `procedural` (how we do things) | `memory_type` field in `POST /memories` |
| **TTL-based expiry** | Automatic memory expiration with configurable TTL per entry | `ttl_seconds` field |
| **Memory decay / auto-prune** | Low-importance old memories auto-tagged `decayed` or deleted, configurable thresholds | Background job, configurable via env vars |
| **Memory scoring** | 3-dimensional scoring: recency × relevance × importance (0.3/0.5/0.2 default weights) | `POST /memories/score` |
| **Full-text search** | FTS5 (SQLite) / GIN tsvector (PostgreSQL) keyword search | `GET /memories/search?q=...` |
| **Semantic search** | OpenAI embedding + pgvector cosine similarity search | `POST /memories/semantic_search` |
| **Fact extraction** | Rule-based (zero dependencies) OR OpenAI LLM-based atomic fact extraction | `POST /memories/extract` |
| **Memory graph** | Visual graph of all memories and their relationships | `GET /graph/data` → `/graph` |

## 🤝 Agent Collaboration

| Capability | Description | API |
|-----------|-------------|-----|
| **Agent inbox** | Async agent-to-agent messaging — leave messages for other agents | `POST /inbox` / `GET /inbox/{agent_id}` |
| **Cross-framework handoff** | Pass context between LangGraph, AutoGen, and CrewAI agents | `POST /handoff/prepare` / `POST /handoff/execute` |
| **Procedural memory** | Track agent action chains, detect repeating workflow patterns automatically | `POST /procedural/record` / `POST /procedural/finalize` |
| **Session lineage** | Parent-child session tracking for multi-step agent conversations | `session.parent_session_id` |
| **Memory propagation** | Automatically propagate memories from child sessions to parent | `propagate_to_parent=true` |

## 🔐 Access Control

| Capability | Description | API |
|-----------|-------------|-----|
| **Per-agent ACL** | Fine-grained read/write/delete permissions per API key | `POST /acl/permissions` |
| **Multi-tenant isolation** | Schema-per-tenant on PostgreSQL with Row-Level Security | `project` field |
| **API key authentication** | Hash-stored bcrypt API keys with activate/deactivate/revoke lifecycle | `POST /admin/keys` |
| **JWT + OAuth2** | Auth0 integration with magic link + Google/Apple/SMS sign-in | `/auth/login` / `/auth/oauth` |
| **Rate limiting** | Per-tier rate limits (Free 5 req/min, Starter 60, Pro 600, Enterprise custom) | Middleware |

## 🔌 Integrations

| Framework | Type | Link |
|-----------|------|------|
| **MCP (Model Context Protocol)** | Drop-in `claude_desktop_config.json` one-liner for Claude Code / Cursor | `memory-bridge-mcp` CLI |
| **LangGraph** | LangGraph adapter — plug into any `StateGraph` | `adapters/langgraph_adapter.py` |
| **AutoGen** | AutoGen adapter — replace built-in memory with Memory Bridge | `adapters/autogen_adapter.py` |
| **CrewAI** | CrewAI adapter — share context across crew members | `adapters/crewai_adapter.py` |
| **OpenAI Agents SDK** | Custom memory plugin using the SDK's tool interface | SDK examples in docs |

## 🚀 Deployment

| Option | Details |
|--------|---------|
| **Hosted (Railway)** | Fully managed — sign up at [memorybridge.app](https://memory-bridge-app-production.up.railway.app) |
| **Self-host (Docker)** | `docker build -t memory-bridge . && docker run -p 8000:8000 memory-bridge` |
| **Self-host (pip)** | `pip install memory-bridge && memory-bridge` |
| **SQLite** | Zero-config, file-based — ideal for development |
| **PostgreSQL** | Production-grade with `pgvector` for semantic search |
| **Redis** | Caching + rate limiting + event bus |

## 📊 Monitoring

| Feature | Description |
|---------|-------------|
| **Health endpoint** | `GET /health` — uptime, request count, avg latency, memory/session counts |
| **Metrics** | Prometheus-style request counter + latency histogram |
| **Audit log** | Immutable hash-chained audit trail for all operations |
| **Admin dashboard** | Web UI for managing API keys, viewing usage, subscription settings |

## 💰 Plans

| Plan | Price | Memories | Sessions | API Keys | Retention | Rate Limit |
|------|-------|----------|----------|----------|-----------|------------|
| Free | $0 | 100 | 10 | 2 | 7 days | 5 req/min |
| Starter | $9/mo | 10K | 100 | 10 | 90 days | 60 req/min |
| Pro | $29/mo | 100K | 1K | 50 | 365 days | 600 req/min |
| Enterprise | Custom | Unlimited | Unlimited | Unlimited | Custom | Custom |
