-- v4: Audit log (append-only)
--
-- Immutable audit trail with SHA-256 chaining for tamper evidence.
--
-- ✅ Backward compatible — CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS only

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    actor_type TEXT NOT NULL,  -- 'api_key', 'user', 'system'
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,      -- 'memory.create', 'memory.delete', 'key.revoke', etc.
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    project_id TEXT,
    ip_address TEXT,
    details TEXT DEFAULT '{}',
    previous_hash TEXT,        -- SHA-256 of previous row (tamper-evident chain)
    hash TEXT                   -- SHA-256 of this row
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log(actor_type, actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_log_project ON audit_log(project_id);
