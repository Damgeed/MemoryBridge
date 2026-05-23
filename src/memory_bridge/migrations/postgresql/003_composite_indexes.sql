-- v3: Composite indexes for common query patterns (PostgreSQL)
--
-- ✅ Backward compatible — CREATE INDEX IF NOT EXISTS only
--
-- Adds composite indexes to accelerate the most frequent query patterns.
-- PostgreSQL supports DESC in index definitions, so we include it for
-- created-at columns which are always queried in descending order.

CREATE INDEX IF NOT EXISTS idx_memories_project_created
    ON memories(project, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_memories_session_key
    ON memories(session_id, key);

CREATE INDEX IF NOT EXISTS idx_memories_project_agent
    ON memories(project, agent_id);

CREATE INDEX IF NOT EXISTS idx_sessions_project_created
    ON sessions(project, created_at DESC);
