-- v3: Composite indexes for common query patterns (SQLite)
--
-- ✅ Backward compatible — CREATE INDEX IF NOT EXISTS only
--
-- Adds composite indexes to accelerate the most frequent query patterns:
--   1. (project, created_at)       — project-scoped time-ordered queries
--   2. (session_id, key)           — memory lookup by key within a session
--   3. (project, agent_id)         — cross-session agent lookups within a project
--   4. (project, created_at)       — session queries scoped by project

CREATE INDEX IF NOT EXISTS idx_memories_project_created
    ON memories(project, created_at);

CREATE INDEX IF NOT EXISTS idx_memories_session_key
    ON memories(session_id, key);

CREATE INDEX IF NOT EXISTS idx_memories_project_agent
    ON memories(project, agent_id);

CREATE INDEX IF NOT EXISTS idx_sessions_project_created
    ON sessions(project, created_at);
