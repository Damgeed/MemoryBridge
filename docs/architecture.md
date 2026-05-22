# Memory Bridge Architecture

## Overview

Memory Bridge is a RESTful middleware service that provides durable, cross-session memory for AI agents. It sits between agents and their storage, enabling context sharing across sessions and between different agents.

## Core Concepts

### Sessions
A session represents a continuous interaction between an agent and its environment. Sessions can have parent-child relationships (e.g., a sub-task spawned from a main task).

### Memories
Key-value pairs stored per-session, per-agent. Each memory can be tagged for selective retrieval.

### Handoff
The protocol by which one agent passes context to another. Includes guardrails to prevent sensitive data leakage.

## Storage Layer

The default storage backend is SQLite via `aiosqlite`. The `MemoryStorage` class provides:

- `store_memory()` — Create or update a memory
- `get_memory()` — Retrieve by ID
- `query_memories()` — Filter by session, agent, tags, or keys
- `delete_memory()` — Remove a memory
- `store_session()` / `get_session()` — Session lifecycle

Storage is swappable — implement the same interface for PostgreSQL, Redis, or any backend.

## Guardrails

The handoff protocol includes:

- **Context size limits** — Prevents runaway context bloat
- **Sensitive key detection** — Blocks credentials, API keys, tokens
- **Tag-based filtering** — Only hand off what's relevant
- **Agent identity validation** — Detects self-handoffs

## Future

- PostgreSQL backend for production
- Redis caching for low-latency reads
- WebSocket streaming for real-time memory sync
- Graph-based memory navigation
