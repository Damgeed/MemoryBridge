# Memory Bridge — Sprint Wrap Plan (Issues #2, #3, #4, #8)

> **Goal:** Complete all remaining sprint board items, then run a team review session with Nova, Rex, Henry, and Fred.

## Execution Phases

| Phase | Tasks | Parallel? |
|-------|-------|-----------|
| 1: Health Metrics (#8) | Upgrade /health with metrics | ✅ Independent |
| 2: Handoff Locking (#3) | Add mutex for concurrent handoffs | ✅ Independent of #8 |
| 3: Tag Junction (#4) | Replace O(n) tag filtering with junction table | ⬜ Depends on #8, #3 |
| 4: Agent Lineages (#2) | Child agents inherit parent context | ⬜ Depends on #3, #4 |
| 5: Team Review | Multi-agent review session | After all code is built |

## Phase 1 — Health Metrics (#8)

**Files:** `src/memory_bridge/main.py`, `tests/test_server.py`
- Track server start time (module-level var)
- Add `count_sessions()` and `count_memories()` to storage.py
- Track request count + total latency for avg_latency_ms
- Return: version, uptime_seconds, sessions_total, memories_total, avg_latency_ms, requests_served

## Phase 2 — Handoff Race Conditions (#3)

**Files:** `src/memory_bridge/handoff.py`, `tests/test_handoff.py`
- Add `asyncio.Lock` per session in HandoffProtocol
- Add `handoff_in_progress` flag per session to prevent concurrent handoffs
- Return clear error when race detected
- Tests: concurrent handoff attempts, graceful rejection

## Phase 3 — Tag Junction Table (#4)

**Files:** `src/memory_bridge/models.py`, `src/memory_bridge/storage.py`, `tests/test_storage.py`
- Add `memory_tags` junction table to schema
- Migration for existing data
- Rewrite tag filtering from Python-side to SQL JOINs
- Update store_memory, query_memories

## Phase 4 — Agent Lineages (#2)

**Files:** `src/memory_bridge/models.py`, `src/memory_bridge/storage.py`, `src/memory_bridge/handoff.py`, `tests/`
- Extend parent_session_id to support automatic memory inheritance
- Child agent can query parent's memories
- Context flows from parent → child
- Optional back-propagation of child updates to parent

## Phase 5 — Team Review

Multi-agent conversation with Henry, Nova, Rex, Fred to review all changes.
