# Changelog

All notable changes to Memory Bridge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.5.0] — 2026-05-22

### Added
- **FTS5 full-text search**: `memory_fts` virtual table with Porter stemming. Search memories by key, value, and tags. `/memories/search?q=...` endpoint with session/agent filtering. Schema migration v5 with backfill.
- **Python client SDK**: `memory_bridge_client.Client` — async wrapper for all 12+ endpoints. Auto auth via Bearer token. Context manager support. 13 integration tests.
- **Prometheus metrics endpoint**: `GET /metrics` returns Prometheus exposition format. Counters for requests (`memory_bridge_http_requests_total`), gauges for memories/sessions/uptime, latency histogram. Exempt from auth and rate limiting.

### Test evolution
```
v0.4.0:  76 tests
v0.5.0:  103 tests (+11 FTS5, +13 SDK, +3 Prometheus)
```

## [0.4.0] — 2026-05-22

### Added
- **Shared metrics table** (`metrics` in SQLite): replaces module-level globals. `request_count`, `total_latency_ms`, `start_time`, and `last_cleanup_at` are now stored in the database. Safe for multi-worker Gunicorn/Uvicorn deployments. Schema migration v4.
- **CORS middleware**: configurable via `MEMORY_BRIDGE_CORS_ORIGINS` (comma-separated, default `*`).
- **Request ID middleware**: each request gets a `X-Request-ID` header (UUID v4). Attached to `request.state.request_id`.
- **Rate limiting**: in-memory sliding-window rate limiter. Configurable via `MEMORY_BRIDGE_RATE_LIMIT` (requests/minute/IP, default 60). Returns 429 with `Retry-After` header.
- **Cleanup monitoring via metrics**: `last_cleanup_at` metric updated on every cleanup cycle. Warning log if cleanup hasn't run in > 2× the configured interval.
- **Increment metric atomicity**: `increment_metric()` uses read-modify-write within a single SQLite connection.

### Changed
- Health endpoint now reads all metrics from the shared metrics table instead of module-level globals.
- `cleanup_expired()` records its timestamp via `record_metric()` instead of a module-level variable.
- Consolidated request pipeline: rate limit → request ID → call → record metrics in a single middleware.
- `last_cleanup_at` removed from `storage.py` module globals.

### Test evolution
```
v0.3.0:  66 tests
v0.4.0:  76 tests  (+3 production hardening, +7 metrics storage)
```

## [0.2.0] — 2026-05-22

### Added
- TTL / eviction policy for memories — each memory can specify `ttl_seconds`
- Background cleanup task that periodically deletes expired memories
- Expired memories are lazily filtered on GET and query
- Column migration for existing databases (backward compatible)
- Configurable via `MEMORY_BRIDGE_CLEANUP_INTERVAL` and `MEMORY_BRIDGE_DEFAULT_TTL` env vars
- 13 new tests (50 total): TTL expiry, renewal, no-TTL, cleanup, API flow
- Smoke test scenarios for TTL create, verify, and expiry filtering
- API key auth middleware (env‑based `MEMORY_BRIDGE_API_KEY`)
- Auth is open‑by‑default — no key needed unless explicitly configured

### Changed
- Bumped version to v0.2.0
- CLI's `reload=True` is now opt-in via `MEMORY_BRIDGE_RELOAD=1`
- Refactored `_row_to_entry()` helper in storage layer
- Removed global DB test contaminant in smoke test

### Fixed
- Python 3.9 compatibility: `.get()` not available on `sqlite3.Row`
- `no such table` error in API-level TTL test (moved to server test suite)
- CI badge count updated to 50 tests

## [0.1.1] — 2026-05-22

### Added
- Docker multi-stage build for production deployment
- `.dockerignore` to keep build context clean
- `CHANGELOG.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`
- `ROADMAP.md` with v0.1–v0.5 milestones
- Issue templates for features, bugs, tech debt, and sprint tasks
- GitHub Actions CI workflow (Python 3.9–3.12)
- Comprehensive smoke test script (19 scenarios)
- Guardrails: sensitive key detection, context size limits

### Changed
- Replaced deprecated `@app.on_event("startup")` with modern FastAPI `lifespan` pattern
- README updated with Docker usage and quick start

### Fixed
- Smoke test boolean casing (Python `True`/`False` vs bash `true`/`false`)
- Smoke test `((PASS++))` exit code bug in bash
- Guardrails test now properly stores and detects sensitive keys
- Server startup handler (`await get_storage()`)

## [0.1.0] — 2026-05-22

### Added
- FastAPI REST server with 9 endpoints
- Async SQLite storage with session/memory tables and indexes
- Agent-to-agent handoff protocol (`prepare` + `execute`)
- Handoff guardrails (sensitive key blocking, context size limits, tag filtering)
- Pydantic models for `MemoryEntry`, `Session`, `HandoffPayload`
- Session chaining (parent-child relationships)
- 37 unit tests covering storage, server, handoff, and models
- CLI entry point (`memory-bridge`)
- `docs/architecture.md` with design documentation
- MIT License
