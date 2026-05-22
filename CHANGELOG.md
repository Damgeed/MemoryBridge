# Changelog

All notable changes to Memory Bridge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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
