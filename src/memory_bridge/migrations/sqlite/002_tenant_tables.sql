-- v2: Multi-tenant tables (SQLite)
--
-- ✅ Backward compatible — CREATE TABLE IF NOT EXISTS only (no DROP/ALTER/RENAME)

CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    stripe_customer_id TEXT,
    tier TEXT NOT NULL DEFAULT 'free',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    organization_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
    role TEXT NOT NULL DEFAULT 'member',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    tenant_schema TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    max_memories INTEGER,
    max_sessions INTEGER,
    memory_ttl_default INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (organization_id, slug)
);
