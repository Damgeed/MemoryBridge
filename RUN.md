# Memory Bridge — Quick Start Guide

## Prerequisites

- Python 3.9+
- pip
- _(Optional)_ Docker & Docker Compose (for PostgreSQL mode)

---

## Quick Start (Self-Hosted — SQLite)

```bash
# 1. Clone
git clone https://github.com/Damgeed/MemoryBridge.git
cd MemoryBridge

# 2. Set up virtual environment
python3 -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# 3. Install
pip install -e ".[dev]"

# 4. Set your API key (required!)
export MEMORY_BRIDGE_API_KEY="your-secret-key-here"

# 5. (Optional) Place the logo
# Copy your logo to: src/memory_bridge/logo.png

# 6. Run
memory-bridge
```

Your server starts at **http://localhost:8000**

---

## Explore

| What | Where |
|------|-------|
| **Playground** | http://localhost:8000/playground |
| **Memory Graph** | http://localhost:8000/graph |
| **API Health** | `curl http://localhost:8000/health` |
| **API Docs** | http://localhost:8000/docs |
| **Badge** | http://localhost:8000/badge |
| **Metrics** | `curl -H "Authorization: Bearer your-key" http://localhost:8000/metrics` |

---

## Quick API Test

```bash
# Create a session
curl -X POST http://localhost:8000/sessions \
  -H "Authorization: Bearer your-key" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "demo", "agent_id": "agent-alice"}'

# Store a memory
curl -X POST http://localhost:8000/memories \
  -H "Authorization: Bearer your-key" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "demo", "agent_id": "agent-alice", "key": "greeting", "value": "Hello from Memory Bridge!"}'

# Search memories
curl "http://localhost:8000/memories/search?q=hello" \
  -H "Authorization: Bearer your-key"
```

---

## Production Mode (PostgreSQL)

```bash
# Start PostgreSQL + Redis
docker compose up -d

# Run in PG mode
MEMORY_BRIDGE_USE_SQLITE=false \
MEMORY_BRIDGE_DATABASE_URL=postgres://mb:mb_dev@localhost/memory_bridge \
MEMORY_BRIDGE_API_KEY="your-secret-key" \
MEMORY_BRIDGE_JWT_SECRET="your-jwt-secret" \
memory-bridge
```

---

## Running Tests

```bash
# Full test suite
pytest tests/ -v

# Just unit tests
pytest tests/unit/ -v

# Integration tests (SQLite)
pytest tests/integration/ -v

# With PostgreSQL (requires running PG)
pytest tests/integration/ -v -m postgres
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MEMORY_BRIDGE_API_KEY` | — | **Required.** Primary API key |
| `MEMORY_BRIDGE_JWT_SECRET` | — | JWT signing secret (required for user auth) |
| `MEMORY_BRIDGE_ALLOW_OPEN` | `false` | Allow unauthenticated access (dev only) |
| `MEMORY_BRIDGE_USE_SQLITE` | `true` | Use SQLite (true) or PostgreSQL (false) |
| `MEMORY_BRIDGE_DATABASE_URL` | `memory_bridge.db` | SQLite path or PostgreSQL DSN |
| `MEMORY_BRIDGE_REDIS_URL` | `redis://localhost:6379` | Redis connection string |
| `MEMORY_BRIDGE_PORT` | `8000` | HTTP server port |
| `MEMORY_BRIDGE_RATE_LIMIT_PER_MINUTE` | `60` | Max requests/min per key |
| `MEMORY_BRIDGE_MAX_BODY_SIZE` | `10485760` | Max request body (10MB) |
| `MEMORY_BRIDGE_MAX_VALUE_SIZE` | `1048576` | Max memory value size (1MB) |
| `STRIPE_SECRET_KEY` | — | Stripe API key (for billing) |
| `STRIPE_WEBHOOK_SECRET` | — | Stripe webhook signing secret |

---

## Deploy

Full deployment guide: `docs/deployment.md`

Deploy checklist: `docs/deploy-checklist.md`

---

## Project Structure

```
src/memory_bridge/
├── main.py              # App factory + middleware
├── auth.py              # API key + JWT authentication
├── config.py            # Settings (env vars)
├── models.py            # Pydantic models
├── controllers/         # 7 API controllers (REST endpoints)
├── services/            # 9 service classes (business logic)
├── repository/          # ABC + SQLite + PostgreSQL + RLS + ShardRouter
├── middleware/          # Rate limiter, tenant resolver, cache headers
├── events/              # EventBus (Redis pub/sub)
├── webhooks/            # Webhook system (HMAC-signed, retry, CRUD)
├── migrations/          # Dual-backend migration files
├── jobs/                # Background jobs (rebalance, metering, email)
├── adapters/            # LangGraph, AutoGen, CrewAI adapters
├── static/              # playground.html, graph.html, logo.svg
└── logo.png             # ← Place your logo here
```

---

**247 tests · 43 endpoints · 14 milestones · Pizza en route 🍕**

Questions? Ping Bud (Danny) or open an issue on GitHub.
