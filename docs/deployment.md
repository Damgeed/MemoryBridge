# Memory Bridge Deployment Guide

> Production deployment configuration for Memory Bridge SaaS — cross-session memory persistence for multi-agent teams.

---

## Architecture Overview

```
                          ┌─────────────┐
                          │   Clients    │
                          │  (agents /   │
                          │   HTTP API)  │
                          └──────┬──────┘
                                 │
                                 ▼
                        ┌────────────────┐
                        │  Cloudflare /   │
                        │     CDN         │
                        └───────┬────────┘
                                │
                                ▼
                       ┌─────────────────┐
                       │  Load Balancer   │
                       │ (ALB / HAProxy)  │
                       └────────┬────────┘
                                │
                 ┌──────────────┼──────────────┐
                 │              │              │
                 ▼              ▼              ▼
          ┌────────────┐ ┌────────────┐ ┌────────────┐
          │  FastAPI   │ │  FastAPI   │ │  FastAPI   │
          │  Worker 1  │ │  Worker 2  │ │  Worker N  │
          └─────┬──────┘ └─────┬──────┘ └─────┬──────┘
                │              │              │
                └──────────────┼──────────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
                    ▼                     ▼
            ┌──────────────┐    ┌──────────────────┐
            │  PostgreSQL  │    │  Redis / Valkey   │
            │     (16+)    │    │       (7+)        │
            │  (persistent │    │  (rate limiting,  │
            │   storage)   │    │   caching, jobs)  │
            └──────────────┘    └──────────────────┘
```

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | >= 3.9 | 3.12+ recommended (used in official Docker image) |
| PostgreSQL | >= 16 | Required for multi-tenant SaaS mode |
| Redis / Valkey | >= 7 | Optional but recommended for rate limiting & caching |
| Docker & Docker Compose | Latest | Recommended for local dev & production |
| Stripe account | — | Required for billing features |

---

## Quick Start (Development)

### Install & Run with SQLite (self-hosted)

```bash
pip install memory-bridge

# Start the server with default SQLite backend
memory-bridge
```

Server starts at `http://localhost:8000`. Health check: `GET /health`.

### Run with PostgreSQL (production-like)

```bash
MEMORY_BRIDGE_USE_SQLITE=false \
MEMORY_BRIDGE_DATABASE_URL=postgres://user:pass@host:5432/memory_bridge \
MEMORY_BRIDGE_API_KEY=your-secret-key \
MEMORY_BRIDGE_JWT_SECRET=your-jwt-secret \
memory-bridge
```

---

## Production Deployment

### Option 1: Docker Compose (Recommended)

The project ships with a `docker-compose.yml` that starts all services:

```bash
cd memory-bridge
docker compose up -d
```

This starts:

| Service | Image | Port | Purpose |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | 5432 | Persistent storage |
| `redis` | `valkey/valkey:8-alpine` | 6379 | Rate limiting, caching |
| `app` | Built from `Dockerfile` | 8000 | Memory Bridge API |

After startup, verify:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/health/ready
```

### Option 2: Manual Deployment

```bash
# 1. Install dependencies
pip install memory-bridge

# 2. Set environment variables (see table below)

# 3. Run migrations (run automatically on startup, or manually):
python -c "from memory_bridge.migrations.runner import MigrationRunner; import asyncio; asyncio.run(MigrationRunner('src/memory_bridge/migrations', 'postgresql').run(conn))"

# 4. Start the server
memory-bridge
```

### Option 3: Multi-Worker (Production)

For production, run multiple workers behind a reverse proxy (e.g., Nginx, ALB):

```bash
uvicorn memory_bridge.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 4 \
  --limit-max-requests 10000 \
  --timeout-keep-alive 65
```

> **Note:** The `memory-bridge` CLI does not support the `--workers` flag. Use `uvicorn` directly for multi-worker deployments.

---

## Environment Variables

All settings are read from environment variables with the `MEMORY_BRIDGE_` prefix via [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).

### Core Settings

| Variable | Default | Description |
|---|---|---|
| `MEMORY_BRIDGE_USE_SQLITE` | `true` | Use SQLite (`true`) or PostgreSQL (`false`) |
| `MEMORY_BRIDGE_DATABASE_URL` | `memory_bridge.db` | SQLite file path, or PostgreSQL DSN (e.g., `postgres://user:pass@host/db`) |
| `MEMORY_BRIDGE_API_KEY` | `""` | Primary API key for service authentication (required in production) |
| `MEMORY_BRIDGE_JWT_SECRET` | `""` | Secret key for JWT token signing (required in production) |
| `MEMORY_BRIDGE_JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `MEMORY_BRIDGE_JWT_EXPIRE_MINUTES` | `60` | JWT token expiration in minutes |
| `MEMORY_BRIDGE_ALLOW_OPEN` | `false` | Allow open access without API key (development only) |

### Connection Pool (PostgreSQL)

| Variable | Default | Description |
|---|---|---|
| `MEMORY_BRIDGE_POOL_MIN_SIZE` | `5` | Minimum database pool connections |
| `MEMORY_BRIDGE_POOL_MAX_SIZE` | `20` | Maximum database pool connections |
| `MEMORY_BRIDGE_POOL_MAX_QUERIES` | `50000` | Max queries per connection before recycling |
| `MEMORY_BRIDGE_POOL_MAX_INACTIVE_CONNECTION_LIFETIME` | `300` | Max inactive connection lifetime (seconds) |
| `MEMORY_BRIDGE_COMMAND_TIMEOUT` | `30` | Query command timeout (seconds) |

### Rate Limiting

| Variable | Default | Description |
|---|---|---|
| `MEMORY_BRIDGE_RATE_LIMIT_PER_MINUTE` | `60` | Max requests per minute per IP |
| `MEMORY_BRIDGE_RATE_LIMIT_BACKEND` | `memory` | Rate limit backend: `memory` or `redis` |

### Redis

| Variable | Default | Description |
|---|---|---|
| `MEMORY_BRIDGE_REDIS_URL` | `redis://localhost:6379` | Redis/Valkey connection string |

### Security

| Variable | Default | Description |
|---|---|---|
| `MEMORY_BRIDGE_MAX_BODY_SIZE` | `10485760` | Max request body size in bytes (10 MB) |
| `MEMORY_BRIDGE_PUBLIC_METRICS` | `false` | Allow unauthenticated access to `/metrics` |
| `MEMORY_BRIDGE_CORS_ORIGINS` | `*` | Allowed CORS origins (comma-separated) |

### Server

| Variable | Default | Description |
|---|---|---|
| `MEMORY_BRIDGE_PORT` | `8000` | HTTP server port (also reads `PORT` env var) |
| `MEMORY_BRIDGE_RELOAD` | `false` | Enable auto-reload on code changes (dev only) |
| `PORT` | `8000` | Alternative port variable (platform convention, e.g., Railway) |

### Memory Lifecycle

| Variable | Default | Description |
|---|---|---|
| `MEMORY_BRIDGE_CLEANUP_INTERVAL` | `300` | Background cleanup interval (seconds, default 5 min) |
| `MEMORY_BRIDGE_DEFAULT_TTL` | `0` | Default memory TTL in seconds (`0` = no default TTL) |

### Stripe / Billing

| Variable | Default | Description |
|---|---|---|
| `STRIPE_SECRET_KEY` | `""` | Stripe API secret key |
| `STRIPE_WEBHOOK_SECRET` | `""` | Stripe webhook signing secret |
| `STRIPE_PRICE_PRO` | `""` | Stripe price ID for "pro" tier |
| `STRIPE_PRICE_ENTERPRISE` | `""` | Stripe price ID for "enterprise" tier |

---

## Deployment Checklist

Use this checklist when deploying to production:

- [ ] **Generate secure secrets:**
  ```bash
  # Generate a 64-char hex key for API_KEY and JWT_SECRET
  openssl rand -hex 32
  ```
- [ ] Set `MEMORY_BRIDGE_API_KEY` to a secure random value
- [ ] Set `MEMORY_BRIDGE_JWT_SECRET` to a secure random value
- [ ] Set `MEMORY_BRIDGE_USE_SQLITE=false`
- [ ] Configure `MEMORY_BRIDGE_DATABASE_URL` with your production PostgreSQL connection string
- [ ] Configure `MEMORY_BRIDGE_REDIS_URL` with your production Redis/Valkey instance
- [ ] Configure `MEMORY_BRIDGE_CORS_ORIGINS` to your specific domain(s) (not `*`)
- [ ] Set `MEMORY_BRIDGE_RATE_LIMIT_BACKEND=redis` for distributed rate limiting
- [ ] Tune `MEMORY_BRIDGE_POOL_MIN_SIZE` and `MEMORY_BRIDGE_POOL_MAX_SIZE` for your expected load
- [ ] Set `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` for billing (if using Stripe)
- [ ] Run database migrations (they run automatically on startup)
- [ ] Verify health endpoints respond correctly
- [ ] Set up monitoring and alerting
- [ ] Configure SSL/TLS (via reverse proxy or Cloudflare)
- [ ] Set `MEMORY_BRIDGE_RELOAD=false`

---

## Database Migrations

### Automatic (default)

Migrations run automatically on application startup, handled by the lifecycle system. The `MigrationRunner` checks for a `schema_version` table and applies any pending SQL files from `src/memory_bridge/migrations/{sqlite,postgresql}/`.

### Manual

```python
from memory_bridge.migrations.runner import MigrationRunner
import asyncio

async def run_migrations():
    runner = MigrationRunner(
        migrations_dir="src/memory_bridge/migrations",
        backend="postgresql",  # or "sqlite"
    )
    # Requires an open asyncpg or aiosqlite connection
    applied = await runner.run(conn)
    print(f"Applied: {applied}")

asyncio.run(run_migrations())
```

---

## Blue-Green Deployment

Memory Bridge supports blue-green (zero-downtime) deployments.

### Principles

1. **Migrations must be backward-compatible** — the old version must work with the new schema
2. **Only ADD, never DROP** — adding columns/indices is safe; removing them requires 2 releases
3. **Migration guard** — the migration runner checks for destructive operations and warns at startup

### Deploy Flow

1. Deploy new version (green) alongside old version (blue)
2. New version runs migrations — these must not break the old version's queries
3. Health checks pass → switch traffic to green
4. Monitor for issues
5. Old version (blue) stays running for rollback capability
6. After 24h, decommission blue

### Rollback Plan

If green has issues:

1. Switch traffic back to blue
2. Green's schema changes are additive (new columns with NULL defaults) — blue ignores them
3. Fix issues on green
4. Deploy fixed green
5. Switch traffic

---

## Backup & Recovery

### PostgreSQL

```bash
# Daily backup
pg_dump -h localhost -U mb memory_bridge > backup_$(date +%Y%m%d).sql

# Restore
psql -h localhost -U mb memory_bridge < backup.sql
```

### SQLite

```bash
cp memory_bridge.db memory_bridge.db.backup_$(date +%Y%m%d)
```

### Automated Backup Script

```bash
#!/bin/bash
# scripts/backup.sh
set -euo pipefail

BACKUP_DIR="/var/backups/memory-bridge"
DB_NAME="memory_bridge"
DB_USER="mb"

mkdir -p "$BACKUP_DIR"
pg_dump -h localhost -U "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_DIR/${DB_NAME}_$(date +%Y%m%d_%H%M%S).sql.gz"

# Keep only last 30 days
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +30 -delete
```

---

## Monitoring

### Health Endpoints

| Endpoint | Type | Description |
|---|---|---|
| `GET /health` | Liveness + detail | Returns service status, uptime, session/memory counts, avg latency |
| `GET /health/ready` | Readiness | Returns 200 if database is connected, 503 if not |
| `GET /health/live` | Liveness | Always returns 200 if process is alive (for Kubernetes) |

### Prometheus Metrics

`GET /metrics` returns Prometheus-formatted metrics at `/metrics`:

| Metric | Type | Description |
|---|---|---|
| `memory_bridge_http_requests_total` | Counter | Total HTTP requests served |
| `memory_bridge_request_latency_seconds` | Histogram | Request latency distribution |
| `memory_bridge_memories` | Gauge | Current number of memories in storage |
| `memory_bridge_sessions` | Gauge | Current number of sessions in storage |
| `memory_bridge_uptime_seconds` | Gauge | Server uptime in seconds |

By default, `/metrics` requires authentication. Set `MEMORY_BRIDGE_PUBLIC_METRICS=true` to allow unauthenticated access.

### Example Prometheus Scrape Config

```yaml
scrape_configs:
  - job_name: 'memory-bridge'
    scrape_interval: 15s
    static_configs:
      - targets: ['localhost:8000']
    authorization:
      credentials: 'your-api-key'
```

### Logging

The application uses structured JSON logging. Log levels follow standard Python conventions (`INFO`, `WARNING`, `ERROR`). For production, ship logs to a centralized system (e.g., Loki, Datadog, CloudWatch).

---

## Scaling

### Horizontal Scaling

Memory Bridge is stateless and can scale horizontally behind a load balancer:

1. **Database:** Use PostgreSQL with connection pooling (PgBouncer recommended for high concurrency).
2. **Redis:** Required for distributed rate limiting (`MEMORY_BRIDGE_RATE_LIMIT_BACKEND=redis`). Without Redis, rate limiting is per-process (in-memory).
3. **Workers:** Run multiple `uvicorn` workers behind a reverse proxy:
   ```bash
   uvicorn memory_bridge.main:app --workers 8 --loop uvloop --http httptools
   ```

### Vertical Scaling

- Tune `MEMORY_BRIDGE_POOL_MIN_SIZE` / `MEMORY_BRIDGE_POOL_MAX_SIZE` for connection throughput
- Increase `MEMORY_BRIDGE_MAX_BODY_SIZE` for large memory payloads
- Adjust `MEMORY_BRIDGE_RATE_LIMIT_PER_MINUTE` for expected traffic

### Production Architecture at Scale

```
                              ┌─────────────┐
                              │   Clients    │
                              └──────┬──────┘
                                     │
                              ┌──────┴──────┐
                              │  Cloudflare  │
                              │   (DDoS,     │
                              │   WAF, SSL)  │
                              └──────┬──────┘
                                     │
                              ┌──────┴──────┐
                              │  Load Balancer│
                              │  (ALB / Nginx)│
                              └──────┬──────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
              ┌─────┴─────┐  ┌──────┴──────┐  ┌──────┴──────┐
              │  FastAPI   │  │   FastAPI   │  │   FastAPI   │
              │  Worker 1  │  │  Worker N   │  │  Worker N   │
              └─────┬─────┘  └──────┬──────┘  └──────┬──────┘
                    │               │                │
                    └───────────────┼────────────────┘
                                    │
                         ┌──────────┴──────────┐
                         │                     │
                         ▼                     ▼
                 ┌───────────────┐    ┌──────────────────┐
                 │   PostgreSQL  │    │  Redis / Valkey   │
                 │  + PgBouncer  │    │     (Cluster)     │
                 │  (Primary +   │    │  (rate limiting,  │
                 │   Replica)    │    │   caching, jobs)  │
                 └───────────────┘    └──────────────────┘
```

---

## Security

- **API Keys:** Hashed with bcrypt before storage
- **JWTs:** Signed with `MEMORY_BRIDGE_JWT_SECRET` using HS256 algorithm
- **Rate Limiting:** 60 requests/min per IP by default, configurable
- **Request Body Limits:** 10 MB maximum body size, configurable
- **CORS:** Configurable allowed origins (default: `*`)
- **Multi-Tenant Isolation:** Row-Level Security (RLS) or schema-per-tenant patterns supported
- **Metrics:** Protected by default — require authentication unless `MEMORY_BRIDGE_PUBLIC_METRICS=true`

---

## Troubleshooting

### Common Issues

| Problem | Cause | Solution |
|---|---|---|
| `Connection refused` on startup | PostgreSQL not running | Check `docker compose ps` or systemd status |
| `Rate limit exceeded` | Too many requests | Increase `MEMORY_BRIDGE_RATE_LIMIT_PER_MINUTE` or check for abusive clients |
| `413 Request body exceeds limit` | Payload too large | Increase `MEMORY_BRIDGE_MAX_BODY_SIZE` |
| `401 Unauthorized` | Missing or invalid API key | Set `MEMORY_BRIDGE_API_KEY` and pass `Authorization: Bearer <key>` header |
| Database connection pool exhausted | Too many concurrent connections | Increase `MEMORY_BRIDGE_POOL_MAX_SIZE` or add PgBouncer |

### Health Check Debugging

```bash
# Liveness — process alive?
curl -s http://localhost:8000/health/live | jq .

# Readiness — database connected?
curl -s http://localhost:8000/health/ready | jq .

# Full health — detailed status
curl -s http://localhost:8000/health | jq .
```

---

## Deployment Platforms

### Railway

The project includes a `railway.toml` for one-click deployment on [Railway](https://railway.app).

### Docker (Custom)

```bash
# Build
docker build -t memory-bridge:latest .

# Run
docker run -d \
  --name memory-bridge \
  -p 8000:8000 \
  -e MEMORY_BRIDGE_USE_SQLITE=false \
  -e MEMORY_BRIDGE_DATABASE_URL=postgres://... \
  -e MEMORY_BRIDGE_API_KEY=... \
  -e MEMORY_BRIDGE_JWT_SECRET=... \
  memory-bridge:latest
```

---

## Runbook

### Startup Sequence

1. PostgreSQL and Redis/Valkey must be healthy and reachable
2. Docker Compose ensures dependency ordering via `depends_on` health checks
3. On startup, the app:
   - Initializes the database connection pool
   - Runs pending schema migrations
   - Seeds initial metrics (`start_time`, `request_count`, `total_latency_ms`)
   - Starts the background cleanup loop
4. Service is ready when `GET /health/ready` returns HTTP 200

### Graceful Shutdown

The FastAPI app handles graceful shutdown:
- Background cleanup task is cancelled
- Database connection pool is closed
- In-flight requests are allowed to complete

### Incident Response

| Severity | Response |
|---|---|
| **Critical** — Service down | 1. Check `GET /health/live` (process alive?) 2. Check `GET /health/ready` (database connected?) 3. Review logs for errors 4. Restart service if unresponsive |
| **High** — Database unavailable | 1. Check PostgreSQL service status 2. Verify connection string 3. Check connection pool saturation 4. Restart database if needed |
| **Medium** — High latency | 1. Check `/metrics` for `memory_bridge_request_latency_seconds` 2. Review connection pool metrics 3. Scale horizontally (add workers) |
| **Low** — Rate limit complaints | 1. Review `MEMORY_BRIDGE_RATE_LIMIT_PER_MINUTE` 2. Check for abusive clients 3. Adjust as needed |

### Regular Maintenance

- **Daily:** Verify backup completion
- **Weekly:** Review error logs, check resource utilization
- **Monthly:** Apply OS and package security updates, review database performance
- **Quarterly:** Rotate API keys and JWT secrets

---

## References

- [FastAPI Deployment Documentation](https://fastapi.tiangolo.com/deployment/)
- [Uvicorn Settings](https://www.uvicorn.org/settings/)
- [PostgreSQL Connection Pooling](https://www.postgresql.org/docs/16/libpq-pgpool.html)
- [Redis Rate Limiting](https://redis.io/glossary/rate-limiting/)
