"""Background jobs for tenant lifecycle management."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# SQL to create tenant schema with all required tables
TENANT_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.sessions (
    session_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    parent_session_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}',
    project TEXT
);

CREATE TABLE IF NOT EXISTS {schema}.memories (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES {schema}.sessions(session_id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ttl_seconds INTEGER,
    project TEXT
);

CREATE INDEX IF NOT EXISTS idx_{sane_schema}_memories_session ON {schema}.memories(session_id);
CREATE INDEX IF NOT EXISTS idx_{sane_schema}_memories_agent ON {schema}.memories(agent_id);
CREATE INDEX IF NOT EXISTS idx_{sane_schema}_memories_key ON {schema}.memories(key);

CREATE INDEX IF NOT EXISTS idx_{sane_schema}_memories_fts
    ON {schema}.memories USING GIN(to_tsvector('english', COALESCE(value::text, '')));

CREATE TABLE IF NOT EXISTS {schema}.memory_tags (
    memory_id TEXT NOT NULL REFERENCES {schema}.memories(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (memory_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_{sane_schema}_memory_tags_tag ON {schema}.memory_tags(tag);

CREATE TABLE IF NOT EXISTS {schema}.schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO {schema}.schema_version (version, applied_at) VALUES (1, NOW());
"""

# SQLite-compatible schema (for local dev without PostgreSQL)
TENANT_SCHEMA_SQLITE_SQL = """
CREATE TABLE IF NOT EXISTS sessions_{sane_schema} (
    session_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    parent_session_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT DEFAULT '{{}}',
    project TEXT
);

CREATE TABLE IF NOT EXISTS memories_{sane_schema} (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    ttl_seconds INTEGER,
    project TEXT
);

CREATE INDEX IF NOT EXISTS idx_memories_{sane_schema}_session ON memories_{sane_schema}(session_id);
CREATE INDEX IF NOT EXISTS idx_memories_{sane_schema}_agent ON memories_{sane_schema}(agent_id);
CREATE INDEX IF NOT EXISTS idx_memories_{sane_schema}_key ON memories_{sane_schema}(key);

CREATE TABLE IF NOT EXISTS memory_tags_{sane_schema} (
    memory_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (memory_id, tag)
);
"""


def generate_tenant_schema_name(project_id: str) -> str:
    """Generate a deterministic schema name from project ID."""
    return f"tenant_{project_id.replace('-', '_')}"


def generate_sane_name(schema_name: str) -> str:
    """Generate a safe identifier for index naming."""
    return schema_name.replace('-', '_').replace('.', '_')


async def provision_tenant_schema(
    pool_or_db,
    project_id: str,
    use_sqlite: bool = False,
) -> str:
    """Provision a tenant schema and create all required tables.

    Args:
        pool_or_db: asyncpg pool or aiosqlite connection
        project_id: The project ID to create a schema for
        use_sqlite: Whether to use SQLite-compatible SQL

    Returns:
        The schema name created
    """
    schema_name = generate_tenant_schema_name(project_id)
    sane = generate_sane_name(schema_name)

    if use_sqlite:
        sql = TENANT_SCHEMA_SQLITE_SQL.format(schema=schema_name, sane_schema=sane)
        await pool_or_db.executescript(sql)
    else:
        sql = TENANT_SCHEMA_SQL.format(schema=schema_name, sane_schema=sane)
        async with pool_or_db.acquire() as conn:
            await conn.execute(sql)

    logger.info("Provisioned tenant schema '%s' for project '%s'", schema_name, project_id)
    return schema_name


async def deprovision_tenant_schema(
    pool_or_db,
    project_id: str,
    use_sqlite: bool = False,
) -> None:
    """Remove a tenant schema."""
    schema_name = generate_tenant_schema_name(project_id)
    if use_sqlite:
        sane = generate_sane_name(schema_name)
        await pool_or_db.executescript(f"DROP TABLE IF EXISTS memories_{sane};")
        await pool_or_db.executescript(f"DROP TABLE IF EXISTS sessions_{sane};")
        await pool_or_db.executescript(f"DROP TABLE IF EXISTS memory_tags_{sane};")
    else:
        async with pool_or_db.acquire() as conn:
            await conn.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
    logger.info("Deprovisioned tenant schema '%s' for project '%s'", schema_name, project_id)
