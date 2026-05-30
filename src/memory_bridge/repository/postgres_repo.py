"""PostgreSQL-backed implementation of MemoryRepository using asyncpg."""

import asyncio
import bcrypt
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

import asyncpg

from ..models import MemoryEntry, MemoryType, Session, Subscription, AgentPermission, InboxMessage, Scratchpad
from ..config import get_settings
from ..services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


def _hash_api_key(plain_key: str) -> str:
    """Hash an API key using bcrypt for secure storage.

    Uses bcrypt (intentionally slow) to resist brute-force attacks.
    """
    return bcrypt.hashpw(plain_key.encode(), bcrypt.gensalt()).decode()


# ── Schema version manifest ──────────────────────────────────────────────────
#   v1  (0.1.0)  Base tables: sessions, memories (with tags column), memory_tags
#   v2  (0.2.0)  Add ttl_seconds column to memories
_SCHEMA_MIGRATIONS: dict[int, str] = {
        2: "ALTER TABLE {schema}.memories ADD COLUMN ttl_seconds INTEGER",
        3: "ALTER TABLE {schema}.memories DROP COLUMN IF EXISTS tags",
        4: (
            "CREATE TABLE IF NOT EXISTS {schema}.metrics ("
            "  key TEXT PRIMARY KEY, "
            "  value TEXT NOT NULL, "
            "  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ")"
        ),
        5: (
            "CREATE INDEX IF NOT EXISTS idx_{schema}_memories_fts "
            "ON {schema}.memories USING GIN (to_tsvector('english', COALESCE(value::text, ''))"
            ")"
        ),
        6: (
            "CREATE TABLE IF NOT EXISTS {schema}.api_keys ("
            "  id TEXT PRIMARY KEY, "
            "  key_hash TEXT NOT NULL UNIQUE, "
            "  label TEXT NOT NULL, "
            "  project_id TEXT, "
            "  is_active BOOLEAN NOT NULL DEFAULT TRUE, "
            "  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
            "  last_used_at TIMESTAMPTZ"
            ")"
        ),
        7: "ALTER TABLE {schema}.memories ADD COLUMN IF NOT EXISTS project TEXT",
        8: "ALTER TABLE {schema}.sessions ADD COLUMN IF NOT EXISTS project TEXT",
        9: (
            "CREATE INDEX IF NOT EXISTS idx_{schema}_memories_project_created "
            "ON {schema}.memories(project, created_at DESC); "
            "CREATE INDEX IF NOT EXISTS idx_{schema}_memories_session_key "
            "ON {schema}.memories(session_id, key); "
            "CREATE INDEX IF NOT EXISTS idx_{schema}_memories_project_agent "
            "ON {schema}.memories(project, agent_id); "
            "CREATE INDEX IF NOT EXISTS idx_{schema}_sessions_project_created "
            "ON {schema}.sessions(project, created_at DESC)"
        ),
        10: (
            "ALTER TABLE {schema}.memories ADD COLUMN IF NOT EXISTS embedding vector(1536); "
            "CREATE INDEX IF NOT EXISTS idx_{schema}_memories_embedding "
            "ON {schema}.memories USING ivfflat (embedding vector_cosine_ops) "
            "WITH (lists = 100)"
        ),
        11: (
            "CREATE TABLE IF NOT EXISTS {schema}.audit_log ("
            "  id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text, "
            "  timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
            "  actor_type TEXT NOT NULL, "
            "  actor_id TEXT NOT NULL, "
            "  action TEXT NOT NULL, "
            "  resource_type TEXT NOT NULL, "
            "  resource_id TEXT, "
            "  project_id TEXT, "
            "  ip_address TEXT, "
            "  details JSONB DEFAULT '{{}}', "
            "  previous_hash TEXT, "
            "  hash TEXT"
            "); "
            "CREATE INDEX IF NOT EXISTS idx_{schema}_audit_log_timestamp "
            "ON {schema}.audit_log(timestamp DESC); "
            "CREATE INDEX IF NOT EXISTS idx_{schema}_audit_log_actor "
            "ON {schema}.audit_log(actor_type, actor_id); "
            "CREATE INDEX IF NOT EXISTS idx_{schema}_audit_log_action "
            "ON {schema}.audit_log(action); "
            "CREATE INDEX IF NOT EXISTS idx_{schema}_audit_log_project "
            "ON {schema}.audit_log(project_id)"
        ),
        12: (
            "CREATE TABLE IF NOT EXISTS {schema}.subscriptions ("
            "  id TEXT PRIMARY KEY, "
            "  organization_id TEXT NOT NULL UNIQUE, "
            "  stripe_customer_id TEXT DEFAULT '', "
            "  tier TEXT NOT NULL DEFAULT 'free', "
            "  status TEXT NOT NULL DEFAULT 'active', "
            "  current_period_start TIMESTAMPTZ, "
            "  current_period_end TIMESTAMPTZ, "
            "  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
            "  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ")"
        ),
        13: (
            "CREATE TABLE IF NOT EXISTS {schema}.oauth_accounts ("
            "  id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text, "
            "  user_id TEXT NOT NULL REFERENCES {schema}.users(id) ON DELETE CASCADE, "
            "  provider TEXT NOT NULL, "
            "  provider_user_id TEXT NOT NULL, "
            "  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
            "  UNIQUE(provider, provider_user_id)"
            ")"
        ),
        14: (
            "ALTER TABLE {schema}.users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT NOT NULL DEFAULT ''"
        ),
        15: (
            "CREATE TABLE IF NOT EXISTS {schema}.agent_permissions ("
            "agent_id TEXT NOT NULL, "
            "project TEXT, "
            "can_read BOOLEAN NOT NULL DEFAULT TRUE, "
            "can_write BOOLEAN NOT NULL DEFAULT TRUE, "
            "can_delete BOOLEAN NOT NULL DEFAULT FALSE, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
            "PRIMARY KEY (agent_id, project)"
            ")"
        ),
        16: (
            "CREATE TABLE IF NOT EXISTS {schema}.webhook_subscriptions ("
            "id TEXT PRIMARY KEY, "
            "url TEXT NOT NULL, "
            "event_types JSONB NOT NULL, "
            "secret TEXT NOT NULL, "
            "project TEXT, "
            "is_active BOOLEAN NOT NULL DEFAULT TRUE, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            "); "
            "CREATE TABLE IF NOT EXISTS {schema}.webhook_deliveries ("
            "id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text, "
            "subscription_id TEXT NOT NULL REFERENCES {schema}.webhook_subscriptions(id) ON DELETE CASCADE, "
            "event_type TEXT NOT NULL, "
            "url TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "status_code INTEGER, "
            "error TEXT, "
            "attempts INTEGER NOT NULL DEFAULT 0, "
            "timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            "); "
            "CREATE INDEX IF NOT EXISTS idx_{schema}_webhook_deliveries_subscription "
            "ON {schema}.webhook_deliveries(subscription_id, timestamp DESC)"
        ),
        17: "ALTER TABLE {schema}.memories ADD COLUMN IF NOT EXISTS memory_type TEXT DEFAULT 'episodic'",
        18: (
            "CREATE TABLE IF NOT EXISTS {schema}.inbox_messages ("
            "id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text, "
            "from_agent_id TEXT NOT NULL, "
            "to_agent_id TEXT NOT NULL, "
            "subject TEXT NOT NULL DEFAULT '', "
            "body TEXT NOT NULL, "
            "priority TEXT NOT NULL DEFAULT 'normal', "
            "is_read BOOLEAN NOT NULL DEFAULT FALSE, "
            "read_at TIMESTAMPTZ, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
            "project TEXT"
            "); "
            "CREATE INDEX IF NOT EXISTS idx_{schema}_inbox_to_agent "
            "ON {schema}.inbox_messages(to_agent_id, is_read, created_at DESC); "
            "CREATE INDEX IF NOT EXISTS idx_{schema}_inbox_project "
            "ON {schema}.inbox_messages(project, to_agent_id)"
        ),
        19: (
            "CREATE TABLE IF NOT EXISTS {schema}.scratchpads ("
            "id TEXT PRIMARY KEY, "
            "session_id TEXT NOT NULL, "
            "agent_id TEXT NOT NULL, "
            "project_id TEXT NOT NULL DEFAULT '', "
            "content JSONB NOT NULL DEFAULT '[]', "
            "contributors JSONB NOT NULL DEFAULT '[]', "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
            "expires_at TIMESTAMPTZ NOT NULL, "
            "ttl_seconds INTEGER NOT NULL DEFAULT 1800"
            "); "
            "CREATE INDEX IF NOT EXISTS idx_{schema}_scratchpads_project "
            "ON {schema}.scratchpads(project_id); "
            "CREATE INDEX IF NOT EXISTS idx_{schema}_scratchpads_expires "
            "ON {schema}.scratchpads(expires_at)"
        ),
        20: "ALTER TABLE {schema}.api_keys ADD COLUMN IF NOT EXISTS scope TEXT",
        21: "ALTER TABLE {schema}.agent_permissions ADD COLUMN IF NOT EXISTS scope TEXT",
        22: "ALTER TABLE {schema}.agent_permissions ADD COLUMN IF NOT EXISTS allowed_agent_types JSONB",
        23: "CREATE INDEX IF NOT EXISTS idx_{schema}_memories_created ON {schema}.memories(created_at DESC)",
        24: "ALTER TABLE {schema}.memories ADD COLUMN IF NOT EXISTS superseded_by TEXT",
    }
# ──────────────────────────────────────────────────────────────────────────────


async def _row_to_entry(row: asyncpg.Record, conn: asyncpg.Connection) -> MemoryEntry:
    """Convert an asyncpg Record to a MemoryEntry, fetching tags from junction table."""
    ttl = row.get("ttl_seconds")

    # Fetch tags from the memory_tags junction table
    tag_rows = await conn.fetch(
        "SELECT tag FROM {schema}.memory_tags WHERE memory_id = $1".format(
            schema="public"  # Will be overridden by the caller
        ),
        row["id"],
    )
    tags = [r["tag"] for r in tag_rows]

    project = row.get("project")

    try:
        memory_type = MemoryType(row.get("memory_type", "episodic"))
    except (ValueError, KeyError):
        memory_type = MemoryType.episodic

    return MemoryEntry(
        id=row["id"],
        session_id=row["session_id"],
        agent_id=row["agent_id"],
        key=row["key"],
        value=json.loads(row["value"]) if isinstance(row["value"], str) else row["value"],
        tags=tags,
        memory_type=memory_type,
        created_at=row["created_at"] if isinstance(row["created_at"], datetime) else datetime.fromisoformat(row["created_at"]),
        updated_at=row["updated_at"] if isinstance(row["updated_at"], datetime) else datetime.fromisoformat(row["updated_at"]),
        ttl_seconds=ttl,
        superseded_by=row.get("superseded_by"),
        project=project,
    )


def _is_expired(entry: MemoryEntry) -> bool:
    """Check if a memory entry has expired based on its TTL."""
    if entry.ttl_seconds is None:
        return False
    elapsed = (datetime.now(timezone.utc) - entry.created_at).total_seconds()
    return elapsed > entry.ttl_seconds


def _filter_expired(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    """Filter out expired entries from a list."""
    return [e for e in entries if not _is_expired(e)]


from ..repository import MemoryRepository


class PostgresMemoryRepository(MemoryRepository):
    """PostgreSQL-backed implementation of the MemoryRepository interface using asyncpg.

    Requires an existing asyncpg connection pool — does not create one.
    """

    def __init__(self, pool: asyncpg.Pool, schema: str = "public"):
        self.pool = pool
        self.schema = schema

    async def initialize(self):
        """Create schema, tables, and run pending migrations.

        Uses a PostgreSQL advisory lock to prevent deadlocks when multiple
        workers attempt to migrate the schema concurrently (C7).
        """
        async with self.pool.acquire() as conn:
            # Acquire advisory lock to serialize concurrent migrations
            await conn.execute("SELECT pg_advisory_lock(123456789)")
            try:
                # Create schema if it doesn't exist
                await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")

                base_ddl = f"""
                    CREATE TABLE IF NOT EXISTS {self.schema}.sessions (
                        session_id TEXT PRIMARY KEY,
                        agent_id TEXT NOT NULL,
                        parent_session_id TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        metadata JSONB DEFAULT '{{}}',
                        project TEXT
                    );

                    CREATE TABLE IF NOT EXISTS {self.schema}.memories (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL REFERENCES {self.schema}.sessions(session_id) ON DELETE CASCADE,
                        agent_id TEXT NOT NULL,
                        key TEXT NOT NULL,
                        value JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        ttl_seconds INTEGER,
                        project TEXT,
                        superseded_by TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_{self.schema}_memories_session
                        ON {self.schema}.memories(session_id);
                    CREATE INDEX IF NOT EXISTS idx_{self.schema}_memories_agent
                        ON {self.schema}.memories(agent_id);
                    CREATE INDEX IF NOT EXISTS idx_{self.schema}_memories_key
                        ON {self.schema}.memories(key);
                    CREATE INDEX IF NOT EXISTS idx_{self.schema}_memories_created
                        ON {self.schema}.memories(created_at DESC);

                    CREATE TABLE IF NOT EXISTS {self.schema}.memory_tags (
                        memory_id TEXT NOT NULL REFERENCES {self.schema}.memories(id) ON DELETE CASCADE,
                        tag TEXT NOT NULL,
                        PRIMARY KEY (memory_id, tag)
                    );

                    CREATE INDEX IF NOT EXISTS idx_{self.schema}_memory_tags_tag
                        ON {self.schema}.memory_tags(tag);

                    CREATE TABLE IF NOT EXISTS {self.schema}.schema_version (
                        version INTEGER PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );

                    CREATE TABLE IF NOT EXISTS {self.schema}.metrics (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );

                    CREATE TABLE IF NOT EXISTS {self.schema}.api_keys (
                        id TEXT PRIMARY KEY,
                        key_hash TEXT NOT NULL UNIQUE,
                        label TEXT NOT NULL,
                        project_id TEXT,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        last_used_at TIMESTAMPTZ
                    );

                    CREATE TABLE IF NOT EXISTS {self.schema}.users (
                        id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
                        email TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL DEFAULT '',
                        name TEXT NOT NULL DEFAULT '',
                        organization_id TEXT NOT NULL DEFAULT '',
                        auth0_sub TEXT DEFAULT '',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );

                    CREATE TABLE IF NOT EXISTS {self.schema}.scratchpads (
                        id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        project_id TEXT NOT NULL DEFAULT '',
                        content JSONB NOT NULL DEFAULT '[]'::jsonb,
                        contributors JSONB NOT NULL DEFAULT '[]'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        expires_at TIMESTAMPTZ NOT NULL,
                        ttl_seconds INTEGER NOT NULL DEFAULT 1800
                    );

                    CREATE INDEX IF NOT EXISTS idx_{self.schema}_scratchpads_project
                        ON {self.schema}.scratchpads(project_id);
                    CREATE INDEX IF NOT EXISTS idx_{self.schema}_scratchpads_expires
                        ON {self.schema}.scratchpads(expires_at);
                """
                await conn.execute(base_ddl)

                # Seed version 1 if this is a fresh database
                await conn.execute(
                    f"INSERT INTO {self.schema}.schema_version (version, applied_at) "
                    f"VALUES (1, NOW()) ON CONFLICT (version) DO NOTHING"
                )

                # Run sequential migrations
                await self._migrate(conn)
            finally:
                await conn.execute("SELECT pg_advisory_unlock(123456789)")

    async def _migrate(self, conn: asyncpg.Connection) -> None:
        """Apply pending schema migrations sequentially.

        Each migration is executed in order. Individual steps that fail
        (e.g. because the column already exists) are logged and skipped
        so that the migration system works on both fresh and upgraded DBs.
        """
        row = await conn.fetchrow(
            f"SELECT COALESCE(MAX(version), 0) AS current_version FROM {self.schema}.schema_version"
        )
        current_version: int = row["current_version"]

        for version, ddl in sorted(_SCHEMA_MIGRATIONS.items()):
            if version <= current_version:
                continue
            try:
                ddl_formatted = ddl.format(schema=self.schema)
                await conn.execute(ddl_formatted)
                await conn.execute(
                    f"INSERT INTO {self.schema}.schema_version (version, applied_at) "
                    f"VALUES ($1, NOW())",
                    version,
                )
                logger.info("Applied schema migration v%d", version)
            except Exception as exc:
                logger.warning(
                    "Schema migration v%d skipped (%s: %s)", version, type(exc).__name__, exc
                )

    async def store_memory(
        self, entry: MemoryEntry, propagate_to_parent: bool = False
    ) -> MemoryEntry:
        """Store a memory entry. Replaces if the same ID already exists.

        If another non-superseded memory exists with the same key + project
        but a different value, the existing memory is marked as superseded
        (conflict resolution).

        Tags are stored exclusively in the memory_tags junction table.

        If propagate_to_parent is True and the session has a parent_session_id,
        also store a reference copy of the memory under the parent session with
        tags augmented with ["propagated:child"].
        """
        async with self.pool.acquire() as conn:
            # ── Conflict resolution ───────────────────────────────────────
            conflicts_resolved = 0
            existing = await self._get_existing_by_key(
                conn, project=entry.project, key=entry.key
            )
            if existing is not None:
                existing_value_str = json.dumps(existing.value, sort_keys=True)
                new_value_str = json.dumps(entry.value, sort_keys=True)
                if existing_value_str != new_value_str:
                    # Mark the old memory as superseded
                    await conn.execute(
                        f"UPDATE {self.schema}.memories SET superseded_by = $1 WHERE id = $2",
                        entry.id, existing.id,
                    )
                    conflicts_resolved = 1

            entry.conflicts_resolved = conflicts_resolved

            # Upsert the memory entry
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.memories
                    (id, session_id, agent_id, key, value, created_at, updated_at, ttl_seconds, superseded_by, project, memory_type)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::timestamptz, $7::timestamptz, $8, $9, $10, $11)
                ON CONFLICT (id) DO UPDATE SET
                    session_id = EXCLUDED.session_id,
                    agent_id = EXCLUDED.agent_id,
                    key = EXCLUDED.key,
                    value = EXCLUDED.value,
                    updated_at = EXCLUDED.updated_at,
                    ttl_seconds = EXCLUDED.ttl_seconds,
                    superseded_by = EXCLUDED.superseded_by,
                    project = EXCLUDED.project,
                    memory_type = EXCLUDED.memory_type
                """,
                entry.id,
                entry.session_id,
                entry.agent_id,
                entry.key,
                json.dumps(entry.value),
                entry.created_at,
                entry.updated_at,
                entry.ttl_seconds,
                entry.superseded_by,
                entry.project,
                entry.memory_type.value,
            )

            # Sync the memory_tags junction table
            await conn.execute(
                f"DELETE FROM {self.schema}.memory_tags WHERE memory_id = $1",
                entry.id,
            )
            for tag in entry.tags:
                await conn.execute(
                    f"INSERT INTO {self.schema}.memory_tags (memory_id, tag) VALUES ($1, $2)",
                    entry.id,
                    tag,
                )

        # Propagate to parent session if requested
        if propagate_to_parent and entry.session_id:
            session = await self.get_session(entry.session_id)
            if session and session.parent_session_id:
                parent_entry = MemoryEntry(
                    session_id=session.parent_session_id,
                    agent_id=entry.agent_id,
                    key=entry.key,
                    value=entry.value,
                    tags=list(set(entry.tags + ["propagated:child"])),
                    created_at=entry.created_at,
                    updated_at=entry.updated_at,
                    ttl_seconds=entry.ttl_seconds,
                    project=entry.project,
                )
                await self.store_memory(parent_entry, propagate_to_parent=False)

        return entry

    async def _get_existing_by_key(
        self, conn: asyncpg.Connection, project: Optional[str], key: str
    ) -> Optional[MemoryEntry]:
        """Find the most recent non-superseded memory with the given key and project.

        Used for conflict resolution inside store_memory.
        """
        if project:
            row = await conn.fetchrow(
                f"SELECT * FROM {self.schema}.memories "
                f"WHERE key = $1 AND project = $2 AND superseded_by IS NULL "
                f"ORDER BY created_at DESC LIMIT 1",
                key, project,
            )
        else:
            row = await conn.fetchrow(
                f"SELECT * FROM {self.schema}.memories "
                f"WHERE key = $1 AND project IS NULL AND superseded_by IS NULL "
                f"ORDER BY created_at DESC LIMIT 1",
                key,
            )
        if row is None:
            return None
        return await self._row_to_entry_schema(row, conn)

    async def get_memory(self, memory_id: str) -> Optional[MemoryEntry]:
        """Retrieve a single memory entry by its ID. Returns None if expired."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {self.schema}.memories WHERE id = $1",
                memory_id,
            )
            if row is None:
                return None

            # Build a custom _row_to_entry that uses the correct schema
            entry = await self._row_to_entry_schema(row, conn)
            if _is_expired(entry):
                # Lazily clean up expired entries on access
                await conn.execute(
                    f"DELETE FROM {self.schema}.memories WHERE id = $1",
                    memory_id,
                )
                return None
            return entry

    async def _row_to_entry_schema(
        self, row: asyncpg.Record, conn: asyncpg.Connection
    ) -> MemoryEntry:
        """Convert an asyncpg Record to a MemoryEntry using self.schema."""
        ttl = row.get("ttl_seconds")

        # Fetch tags from the memory_tags junction table
        tag_rows = await conn.fetch(
            f"SELECT tag FROM {self.schema}.memory_tags WHERE memory_id = $1",
            row["id"],
        )
        tags = [r["tag"] for r in tag_rows]

        project = row.get("project")

        try:
            memory_type = MemoryType(row.get("memory_type", "episodic"))
        except (ValueError, KeyError):
            memory_type = MemoryType.episodic

        # asyncpg returns JSONB as dict/list, timestamptz as datetime
        value = row["value"] if isinstance(row["value"], (dict, list)) else json.loads(row["value"])
        created_at = row["created_at"] if isinstance(row["created_at"], datetime) else datetime.fromisoformat(row["created_at"])
        updated_at = row["updated_at"] if isinstance(row["updated_at"], datetime) else datetime.fromisoformat(row["updated_at"])

        return MemoryEntry(
            id=row["id"],
            session_id=row["session_id"],
            agent_id=row["agent_id"],
            key=row["key"],
            value=value,
            tags=tags,
            memory_type=memory_type,
            created_at=created_at,
            updated_at=updated_at,
            ttl_seconds=ttl,
            superseded_by=row.get("superseded_by"),
            project=project,
        )

    async def query_memories(
        self,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        keys: Optional[list[str]] = None,
        limit: int = 50,
        offset: int = 0,
        project: Optional[str] = None,
    ) -> list[MemoryEntry]:
        """Query memories with optional filters. Tags are filtered via SQL JOIN.
        Expired memories are filtered out.
        """
        conditions: list[str] = []
        params: list = []
        param_idx = 1

        if session_id:
            conditions.append(f"session_id = ${param_idx}")
            params.append(session_id)
            param_idx += 1
        if agent_id:
            conditions.append(f"agent_id = ${param_idx}")
            params.append(agent_id)
            param_idx += 1
        if keys:
            placeholders = ",".join(f"${param_idx + i}" for i in range(len(keys)))
            conditions.append(f"key IN ({placeholders})")
            params.extend(keys)
            param_idx += len(keys)
        if tags:
            unique_tags = list(set(tags))
            placeholders = ",".join(f"${param_idx + i}" for i in range(len(unique_tags)))
            conditions.append(
                f"id IN ("
                f"  SELECT memory_id FROM {self.schema}.memory_tags "
                f"  WHERE tag IN ({placeholders}) "
                f"  GROUP BY memory_id "
                f"  HAVING COUNT(DISTINCT tag) = ${param_idx + len(unique_tags)}"
                f")"
            )
            params.extend(unique_tags)
            params.append(len(unique_tags))
            param_idx += len(unique_tags) + 1
        if project:
            conditions.append(f"project = ${param_idx}")
            params.append(project)
            param_idx += 1

        # Exclude superseded memories from normal queries
        conditions.append("superseded_by IS NULL")

        where_clause = " AND ".join(conditions) if conditions else "TRUE"

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT id, session_id, agent_id, key, value, created_at, updated_at, ttl_seconds, project "
                f"FROM {self.schema}.memories "
                f"WHERE {where_clause} "
                f"ORDER BY created_at DESC "
                f"LIMIT ${param_idx} OFFSET ${param_idx + 1}",
                *params, limit, offset,
            )

            results: list[MemoryEntry] = []
            expired_ids: list[str] = []
            for row in rows:
                entry = await self._row_to_entry_schema(row, conn)
                if _is_expired(entry):
                    expired_ids.append(entry.id)
                    continue
                results.append(entry)

            # Lazily clean up expired entries
            if expired_ids:
                id_placeholders = ",".join(f"${i + 1}" for i in range(len(expired_ids)))
                await conn.execute(
                    f"DELETE FROM {self.schema}.memories WHERE id IN ({id_placeholders})",
                    *expired_ids,
                )

            return results

    async def search_memories(
        self,
        query: str,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        project: Optional[str] = None,
    ) -> list[MemoryEntry]:
        """Full-text search across memory values using PostgreSQL tsvector.

        Returns matching memories ordered by creation time (newest first).
        Filters by session_id and/or agent_id if provided.
        Expired memories are filtered out.
        """
        conditions = [
            f"to_tsvector('english', COALESCE(value::text, '')) @@ plainto_tsquery('english', $1)"
        ]
        params = [query]
        param_idx = 2

        if session_id:
            conditions.append(f"session_id = ${param_idx}")
            params.append(session_id)
            param_idx += 1
        if agent_id:
            conditions.append(f"agent_id = ${param_idx}")
            params.append(agent_id)
            param_idx += 1
        if project:
            conditions.append(f"project = ${param_idx}")
            params.append(project)
            param_idx += 1

        # Exclude superseded memories from search results
        conditions.append("superseded_by IS NULL")

        where_clause = " AND ".join(conditions)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT id, session_id, agent_id, key, value, created_at, updated_at, ttl_seconds, project "
                f"FROM {self.schema}.memories "
                f"WHERE {where_clause} "
                f"ORDER BY created_at DESC "
                f"LIMIT ${param_idx} OFFSET ${param_idx + 1}",
                *params, limit, offset,
            )

            results: list[MemoryEntry] = []
            expired_ids: list[str] = []
            for row in rows:
                entry = await self._row_to_entry_schema(row, conn)
                if _is_expired(entry):
                    expired_ids.append(entry.id)
                    continue
                results.append(entry)

            # Lazily clean up expired entries
            if expired_ids:
                id_placeholders = ",".join(f"${i + 1}" for i in range(len(expired_ids)))
                await conn.execute(
                    f"DELETE FROM {self.schema}.memories WHERE id IN ({id_placeholders})",
                    *expired_ids,
                )

            return results

    async def search_memories_semantic(
        self,
        query_vector: list[float],
        project: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryEntry]:
        """Search memories by semantic similarity using pgvector.

        Uses cosine distance (<=>) to rank memories by embedding similarity.
        Filters by project if provided.
        Expired memories are filtered out.
        """
        conditions: list[str] = []
        params: list = []
        param_idx = 1

        if project:
            conditions.append(f"m.project = ${param_idx}")
            params.append(project)
            param_idx += 1

        where = " AND ".join(conditions) if conditions else "TRUE"

        # Pad or truncate vector to 1536 dimensions
        vec = query_vector[:1536] if len(query_vector) >= 1536 else query_vector + [0.0] * (1536 - len(query_vector))
        vec_str = "[" + ",".join(str(v) for v in vec) + "]"

        sql = f"""
            SELECT m.id, m.session_id, m.agent_id, m.key, m.value,
                   m.created_at, m.updated_at, m.ttl_seconds, m.project
            FROM {self.schema}.memories m
            WHERE {where}
            ORDER BY m.embedding <=> ${param_idx}::vector
            LIMIT ${param_idx + 1} OFFSET ${param_idx + 2}
        """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, *params, vec_str, limit, offset)
            results: list[MemoryEntry] = []
            expired_ids: list[str] = []
            for row in rows:
                entry = await self._row_to_entry_schema(row, conn)
                if _is_expired(entry):
                    expired_ids.append(entry.id)
                    continue
                results.append(entry)

            # Lazily clean up expired entries
            if expired_ids:
                id_placeholders = ",".join(f"${i + 1}" for i in range(len(expired_ids)))
                await conn.execute(
                    f"DELETE FROM {self.schema}.memories WHERE id IN ({id_placeholders})",
                    *expired_ids,
                )

            return results

    # ── Embedding Storage ──────────────────────────────────────────────────

    async def store_embedding(self, memory_id: str, embedding: list[float]) -> None:
        """Store an embedding vector for a memory entry.

        Uses the existing 'embedding' column on the memories table
        (added by schema migration v10). If the column doesn't exist
        (pgvector not available), falls back silently.
        """
        vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(
                    f"UPDATE {self.schema}.memories SET embedding = $1::vector "
                    f"WHERE id = $2",
                    vec_str, memory_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to store embedding for %s (pgvector may not be installed): %s",
                    memory_id, exc,
                )

    async def get_embedding(self, memory_id: str) -> Optional[list[float]]:
        """Retrieve the stored embedding for a memory entry."""
        async with self.pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    f"SELECT embedding FROM {self.schema}.memories WHERE id = $1",
                    memory_id,
                )
                if row is None or row["embedding"] is None:
                    return None
                return list(row["embedding"])
            except Exception as exc:
                logger.warning(
                    "Failed to get embedding for %s (pgvector may not be installed): %s",
                    memory_id, exc,
                )
                return None

    async def search_by_vector(
        self, embedding: list[float], limit: int = 10
    ) -> list[str]:
        """Search memory IDs by vector similarity.

        Tries pgvector cosine distance (<=>) first. If pgvector is not
        available, falls back to loading all embeddings and computing
        cosine similarity in Python.
        """
        vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
        async with self.pool.acquire() as conn:
            try:
                # Try pgvector
                rows = await conn.fetch(
                    f"SELECT id FROM {self.schema}.memories "
                    f"WHERE embedding IS NOT NULL "
                    f"ORDER BY embedding <=> $1::vector "
                    f"LIMIT $2",
                    vec_str, limit,
                )
                return [row["id"] for row in rows]
            except Exception:
                # pgvector not available — fall back to brute-force in Python
                logger.info("pgvector not available, falling back to Python brute-force search")
                pass

        # Brute-force fallback: load all embeddings
        async with self.pool.acquire() as conn:
            try:
                rows = await conn.fetch(
                    f"SELECT id, embedding FROM {self.schema}.memories "
                    f"WHERE embedding IS NOT NULL"
                )
            except Exception as exc:
                logger.warning(
                    "Failed to read embeddings for vector search: %s", exc
                )
                return []

        if not rows:
            return []

        scored: list[tuple[float, str]] = []
        for row in rows:
            mem_id = row["id"]
            stored_emb = list(row["embedding"])
            sim = EmbeddingService.cosine_similarity(embedding, stored_emb)
            scored.append((sim, mem_id))

        scored.sort(key=lambda x: -x[0])
        return [mem_id for _, mem_id in scored[:limit]]

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory entry by ID. Returns True if a row was deleted."""
        async with self.pool.acquire() as conn:
            # CASCADE will handle memory_tags
            result = await conn.execute(
                f"DELETE FROM {self.schema}.memories WHERE id = $1",
                memory_id,
            )
            # asyncpg execute returns "DELETE <count>"
            count = int(result.split()[-1]) if result else 0
            return count > 0

    async def count_memories(self) -> int:
        """Return the total number of memories in storage."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT COUNT(*) AS count FROM {self.schema}.memories"
            )
            return row["count"]

    async def store_session(self, session: Session) -> Session:
        """Store a session record. Replaces if the same session_id exists."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.sessions
                    (session_id, agent_id, parent_session_id, created_at, metadata, project)
                VALUES ($1, $2, $3, $4::timestamptz, $5::jsonb, $6)
                ON CONFLICT (session_id) DO UPDATE SET
                    agent_id = EXCLUDED.agent_id,
                    parent_session_id = EXCLUDED.parent_session_id,
                    created_at = EXCLUDED.created_at,
                    metadata = EXCLUDED.metadata,
                    project = EXCLUDED.project
                """,
                session.session_id,
                session.agent_id,
                session.parent_session_id,
                session.created_at,
                json.dumps(session.metadata),
                session.project,
            )
        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Retrieve a session by its ID."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {self.schema}.sessions WHERE session_id = $1",
                session_id,
            )
            if row is None:
                return None

            metadata = row["metadata"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)

            created_at = row["created_at"]
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)

            return Session(
                session_id=row["session_id"],
                agent_id=row["agent_id"],
                parent_session_id=row["parent_session_id"],
                created_at=created_at,
                metadata=metadata,
                project=row.get("project"),
            )

    async def get_session_lineage(self, session_id: str) -> list[str]:
        """Follow parent_session_id chain up recursively.
        Returns ordered list [session_id, parent_id, grandparent_id, ...].
        Raises ValueError if depth exceeds 10 (possible cycle or runaway chain).
        """
        lineage: list[str] = []
        current_id: Optional[str] = session_id
        depth = 0
        while current_id and depth < 10:
            lineage.append(current_id)
            session = await self.get_session(current_id)
            if session is None:
                break
            current_id = session.parent_session_id
            depth += 1
        if depth >= 10 and current_id is not None:
            raise ValueError(
                "Session lineage exceeds maximum depth of 10. Possible cycle or runaway chain."
            )
        return lineage

    async def count_sessions(self) -> int:
        """Return the total number of sessions in storage."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT COUNT(*) AS count FROM {self.schema}.sessions"
            )
            return row["count"]

    async def cleanup_expired(self) -> int:
        """Delete all expired memories. Returns the number of rows deleted."""
        async with self.pool.acquire() as conn:
            # Fetch all memories with a TTL set
            rows = await conn.fetch(
                f"SELECT id, created_at, ttl_seconds FROM {self.schema}.memories "
                f"WHERE ttl_seconds IS NOT NULL"
            )
            now = datetime.now(timezone.utc)
            expired_ids: list[str] = []
            for row in rows:
                created_at = row["created_at"]
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at)
                elapsed = (now - created_at).total_seconds()
                if elapsed > row["ttl_seconds"]:
                    expired_ids.append(row["id"])

            if not expired_ids:
                return 0

            placeholders = ",".join(f"${i + 1}" for i in range(len(expired_ids)))
            result = await conn.execute(
                f"DELETE FROM {self.schema}.memories WHERE id IN ({placeholders})",
                *expired_ids,
            )
            # CASCADE will handle memory_tags cleanup
            count = int(result.split()[-1]) if result else 0
            if count:
                logger.info("Cleaned up %d expired memories", count)
            # Record cleanup timestamp in shared metrics
            await self.record_metric("last_cleanup_at", datetime.now(timezone.utc).isoformat())
            return count

    # ── Inbox Message Management ──────────────────────────────────────────────

    async def send_inbox_message(self, msg: InboxMessage) -> InboxMessage:
        """Send an inbox message from one agent to another."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {self.schema}.inbox_messages
                    (id, from_agent_id, to_agent_id, subject, body, priority, is_read, created_at, project)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::timestamptz, $9)""",
                msg.id, msg.from_agent_id, msg.to_agent_id, msg.subject,
                msg.body, msg.priority, msg.read, msg.created_at, msg.project,
            )
        return msg

    async def get_inbox_messages(
        self, to_agent_id: str, unread_only: bool = False,
        limit: int = 50, offset: int = 0, project: Optional[str] = None
    ) -> tuple[list[InboxMessage], int]:
        """Get inbox messages for an agent. Returns (messages, total_count)."""
        async with self.pool.acquire() as conn:
            conditions = ["to_agent_id = $1"]
            params = [to_agent_id]
            param_idx = 2
            if unread_only:
                conditions.append(f"is_read = FALSE")
            if project:
                conditions.append(f"project = ${param_idx}")
                params.append(project)
                param_idx += 1

            where = " AND ".join(conditions)

            # Count
            row = await conn.fetchval(
                f"SELECT COUNT(*) FROM {self.schema}.inbox_messages WHERE {where}", *params
            )
            total = row or 0

            # Fetch
            rows = await conn.fetch(
                f"SELECT * FROM {self.schema}.inbox_messages WHERE {where} ORDER BY created_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}",
                *params, limit, offset,
            )
            messages = []
            for row in rows:
                messages.append(InboxMessage(
                    id=row["id"],
                    from_agent_id=row["from_agent_id"],
                    to_agent_id=row["to_agent_id"],
                    subject=row.get("subject") or "",
                    body=row["body"],
                    priority=row.get("priority") or "normal",
                    read=row.get("is_read", False),
                    read_at=row.get("read_at"),
                    created_at=row["created_at"] if isinstance(row["created_at"], datetime) else datetime.fromisoformat(row["created_at"]),
                    project=row.get("project"),
                ))
            return messages, total

    async def acknowledge_inbox_message(self, message_id: str) -> bool:
        """Mark an inbox message as read. Returns True if found."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"UPDATE {self.schema}.inbox_messages SET is_read = TRUE, read_at = NOW() WHERE id = $1",
                message_id,
            )
            return result != "UPDATE 0"

    async def count_unread_inbox(self, to_agent_id: str, project: Optional[str] = None) -> int:
        """Count unread inbox messages for an agent."""
        async with self.pool.acquire() as conn:
            if project:
                row = await conn.fetchval(
                    f"SELECT COUNT(*) FROM {self.schema}.inbox_messages WHERE to_agent_id = $1 AND is_read = FALSE AND project = $2",
                    to_agent_id, project,
                )
            else:
                row = await conn.fetchval(
                    f"SELECT COUNT(*) FROM {self.schema}.inbox_messages WHERE to_agent_id = $1 AND is_read = FALSE",
                    to_agent_id,
                )
            return row or 0

    # ── API Key Management ────────────────────────────────────────────────────

    async def create_api_key(self, label: str, project_id: Optional[str] = None, scope: Optional[str] = None) -> dict:
        """Create a new API key. Returns the full key info including the plaintext key (show once)."""
        plain_key = f"mb_{secrets.token_hex(24)}"
        key_hash = _hash_api_key(plain_key)
        key_id = str(uuid4())
        now = datetime.now(timezone.utc)

        async with self.pool.acquire() as conn:
            await conn.execute(
                f"INSERT INTO {self.schema}.api_keys "
                f"(id, key_hash, label, project_id, scope, is_active, created_at) "
                f"VALUES ($1, $2, $3, $4, $5, TRUE, $6::timestamptz)",
                key_id, key_hash, label, project_id, scope, now,
            )

        return {
            "id": key_id,
            "key": plain_key,
            "label": label,
            "project_id": project_id,
            "scope": scope,
            "is_active": True,
            "created_at": now.isoformat(),
        }

    async def list_api_keys(self) -> list[dict]:
        """List all API keys (without the actual key value, only hash)."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT id, label, project_id, scope, is_active, created_at, last_used_at "
                f"FROM {self.schema}.api_keys "
                f"ORDER BY created_at DESC"
            )
            results = []
            for row in rows:
                entry = dict(row)
                # Convert datetimes to isoformat for serialization
                if isinstance(entry.get("created_at"), datetime):
                    entry["created_at"] = entry["created_at"].isoformat()
                if isinstance(entry.get("last_used_at"), datetime):
                    entry["last_used_at"] = entry["last_used_at"].isoformat()
                results.append(entry)
            return results

    async def revoke_api_key(self, key_id: str) -> bool:
        """Revoke an API key by setting is_active=FALSE."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"UPDATE {self.schema}.api_keys SET is_active = FALSE WHERE id = $1",
                key_id,
            )
            count = int(result.split()[-1]) if result else 0
            return count > 0

    async def reactivate_api_key(self, key_id: str) -> bool:
        """Reactivate a previously deactivated API key."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"UPDATE {self.schema}.api_keys SET is_active = TRUE WHERE id = $1",
                key_id,
            )
            count = int(result.split()[-1]) if result else 0
            return count > 0

    async def deactivate_excess_keys(self, project_id: str, max_allowed: int) -> int:
        """Deactivate excess API keys for an org, keeping the newest max_allowed active."""
        keys = await self.list_api_keys()
        org_active = [k for k in keys if k.get('project_id') == project_id and k.get('is_active') is True]
        if len(org_active) <= max_allowed:
            return 0
        # Sort by created_at desc, keep max_allowed, deactivate rest
        org_active.sort(key=lambda k: k.get('created_at', ''), reverse=True)
        to_deactivate = org_active[max_allowed:]
        deactivated = 0
        for k in to_deactivate:
            if await self.revoke_api_key(k['id']):
                deactivated += 1
        return deactivated

    async def authenticate_key(self, plain_key: str) -> Optional[dict]:
        """Authenticate a plaintext API key. Returns key info or None if invalid."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT id, key_hash, label, project_id, is_active "
                f"FROM {self.schema}.api_keys WHERE is_active = TRUE",
            )

            matched_row = None
            for row in rows:
                try:
                    if bcrypt.checkpw(plain_key.encode(), row["key_hash"].encode()):
                        matched_row = row
                        break
                except ValueError:
                    # Invalid hash format, skip
                    continue

            if matched_row is None:
                return None

            # Update last_used_at
            await conn.execute(
                f"UPDATE {self.schema}.api_keys SET last_used_at = NOW() WHERE id = $1",
                matched_row["id"],
            )

            return {
                "id": matched_row["id"],
                "label": matched_row["label"],
                "project_id": matched_row["project_id"],
            }

    # ── Metrics ───────────────────────────────────────────────────────────────

    async def record_metric(self, key: str, value: Any) -> None:
        """Upsert a metric value (converted to JSON string)."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.metrics (key, value, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = NOW()
                """,
                key, json.dumps(value),
            )

    async def get_metric(self, key: str) -> Optional[Any]:
        """Get a metric value (parsed from JSON string). Returns None if not set."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT value FROM {self.schema}.metrics WHERE key = $1",
                key,
            )
            if row is None:
                return None
            return json.loads(row["value"])

    async def get_all_metrics(self) -> dict[str, Any]:
        """Get all metrics as a dict mapping key to parsed value."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT key, value FROM {self.schema}.metrics"
            )
            return {row["key"]: json.loads(row["value"]) for row in rows}

    async def increment_metric(self, key: str, delta: int = 1) -> int:
        """Atomically increment a numeric metric. Returns the new value.

        Uses atomic UPSERT to eliminate read-then-write race conditions
        that can drop increments under concurrent load.
        Initializes to 0 if the key doesn't exist yet.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {self.schema}.metrics (key, value, updated_at)
                VALUES ($1, '0', NOW())
                ON CONFLICT (key) DO UPDATE SET
                    value = (
                        (COALESCE(NULLIF({self.schema}.metrics.value, ''), '0')::integer + $2)::text
                    ),
                    updated_at = NOW()
                RETURNING value::text::int
                """,
                key,
                delta,
            )
            return row[0] if row else delta

    async def initialize_metric(self, key: str, default_value: Any) -> None:
        """Set a metric only if it doesn't exist yet.

        This is designed for startup counters (start_time, request_count)
        that should be set once and never overwritten.
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.metrics (key, value, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (key) DO NOTHING
                """,
                key, json.dumps(default_value),
            )

    # ── Audit Log ───────────────────────────────────────────────────────────

    async def record_audit_entry(
        self,
        id: str,
        timestamp: str,
        actor_type: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: Optional[str],
        project_id: Optional[str],
        ip_address: Optional[str],
        details: Any,
        previous_hash: Optional[str],
        hash: str,
    ) -> None:
        """Insert an audit log entry."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {self.schema}.audit_log
                   (id, timestamp, actor_type, actor_id, action,
                    resource_type, resource_id, project_id, ip_address,
                    details, previous_hash, hash)
                   VALUES ($1, $2::timestamptz, $3, $4, $5, $6, $7, $8, $9,
                           $10::jsonb, $11, $12)""",
                id, timestamp, actor_type, actor_id, action,
                resource_type, resource_id, project_id, ip_address,
                json.dumps(details) if isinstance(details, dict) else details,
                previous_hash, hash,
            )

    async def get_last_audit_hash(self) -> Optional[str]:
        """Get the hash of the most recent audit entry."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT hash FROM {self.schema}.audit_log "
                f"ORDER BY timestamp DESC LIMIT 1"
            )
            return row["hash"] if row else None

    async def get_all_audit_entries(self) -> list[dict]:
        """Return all audit entries ordered by timestamp ascending."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM {self.schema}.audit_log "
                f"ORDER BY timestamp ASC"
            )
            results = []
            for row in rows:
                entry = dict(row)
                # Convert datetimes to ISO strings for consistency
                if isinstance(entry.get("timestamp"), datetime):
                    entry["timestamp"] = entry["timestamp"].isoformat()
                if isinstance(entry.get("details"), (dict, list)):
                    entry["details"] = entry["details"]
                results.append(entry)
            return results

    # ── Subscription Management ──────────────────────────────────────────────

    async def store_subscription(self, sub: Subscription) -> Subscription:
        """Store a subscription. Upserts if the same id exists."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.subscriptions
                    (id, organization_id, stripe_customer_id, tier, status,
                     current_period_start, current_period_end, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6::timestamptz, $7::timestamptz, $8::timestamptz, $9::timestamptz)
                ON CONFLICT (organization_id) DO UPDATE SET
                    id = EXCLUDED.id,
                    tier = EXCLUDED.tier,
                    stripe_customer_id = EXCLUDED.stripe_customer_id,
                    status = EXCLUDED.status,
                    current_period_start = EXCLUDED.current_period_start,
                    current_period_end = EXCLUDED.current_period_end,
                    updated_at = EXCLUDED.updated_at
                """,
                sub.id,
                sub.organization_id,
                sub.stripe_customer_id,
                sub.tier,
                sub.status,
                sub.current_period_start,
                sub.current_period_end,
                sub.created_at,
                sub.updated_at,
            )
        return sub

    async def get_subscription_by_org(self, organization_id: str) -> Optional[Subscription]:
        """Get subscription by organization ID."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {self.schema}.subscriptions WHERE organization_id = $1",
                organization_id,
            )
            if row is None:
                return None
            return Subscription(
                id=row["id"] or "",
                organization_id=row["organization_id"],
                stripe_customer_id=row["stripe_customer_id"] or "",
                tier=row["tier"] or "free",
                status=row["status"] or "active",
                current_period_start=row["current_period_start"],
                current_period_end=row["current_period_end"],
                created_at=row["created_at"] if isinstance(row["created_at"], datetime) else datetime.fromisoformat(row["created_at"]),
                updated_at=row["updated_at"] if isinstance(row["updated_at"], datetime) else datetime.fromisoformat(row["updated_at"]),
            )

    async def get_subscription_by_stripe_customer(self, customer_id: str) -> Optional[Subscription]:
        """Get subscription by Stripe customer ID."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {self.schema}.subscriptions WHERE stripe_customer_id = $1",
                customer_id,
            )
            if row is None:
                return None
            return Subscription(
                id=row["id"] or "",
                organization_id=row["organization_id"],
                stripe_customer_id=row["stripe_customer_id"] or "",
                tier=row["tier"] or "free",
                status=row["status"] or "active",
                current_period_start=row["current_period_start"],
                current_period_end=row["current_period_end"],
                created_at=row["created_at"] if isinstance(row["created_at"], datetime) else datetime.fromisoformat(row["created_at"]),
                updated_at=row["updated_at"] if isinstance(row["updated_at"], datetime) else datetime.fromisoformat(row["updated_at"]),
            )

    async def get_subscription_by_id(self, sub_id: str) -> Optional[Subscription]:
        """Get subscription by its Stripe subscription ID. Returns None if not found."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {self.schema}.subscriptions WHERE id = $1",
                sub_id,
            )
            if row is None:
                return None
            return Subscription(
                id=row["id"] or "",
                organization_id=row["organization_id"],
                stripe_customer_id=row["stripe_customer_id"] or "",
                tier=row["tier"] or "free",
                status=row["status"] or "active",
                current_period_start=row["current_period_start"],
                current_period_end=row["current_period_end"],
                created_at=row["created_at"] if isinstance(row["created_at"], datetime) else datetime.fromisoformat(row["created_at"]),
                updated_at=row["updated_at"] if isinstance(row["updated_at"], datetime) else datetime.fromisoformat(row["updated_at"]),
            )

    async def update_subscription_tier(self, sub_id: str, tier: str) -> Optional[Subscription]:
        """Update the tier of a subscription by Stripe subscription ID."""
        async with self.pool.acquire() as conn:
            now = datetime.now(timezone.utc)
            result = await conn.execute(
                f"UPDATE {self.schema}.subscriptions SET tier = $1, updated_at = $2::timestamptz WHERE id = $3",
                tier, now, sub_id,
            )
            count = int(result.split()[-1]) if result else 0
            if count == 0:
                return None
            row = await conn.fetchrow(
                f"SELECT * FROM {self.schema}.subscriptions WHERE id = $1",
                sub_id,
            )
            if row is None:
                return None
            return Subscription(
                id=row["id"] or "",
                organization_id=row["organization_id"],
                stripe_customer_id=row["stripe_customer_id"] or "",
                tier=row["tier"] or "free",
                status=row["status"] or "active",
                current_period_start=row["current_period_start"],
                current_period_end=row["current_period_end"],
                created_at=row["created_at"] if isinstance(row["created_at"], datetime) else datetime.fromisoformat(row["created_at"]),
                updated_at=row["updated_at"] if isinstance(row["updated_at"], datetime) else datetime.fromisoformat(row["updated_at"]),
            )

    # ── User Management ───────────────────────────────────────────────────────

    async def create_user(self, user) -> dict:
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.users
                    (id, email, password_hash, name, organization_id, auth0_sub, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7::timestamptz, $8::timestamptz)
                ON CONFLICT (email) DO NOTHING
                """,
                user.id, user.email, user.password_hash, user.name,
                user.organization_id, user.auth0_sub, user.created_at, user.updated_at,
            )
        return {"id": user.id, "email": user.email, "name": user.name, "organization_id": user.organization_id}

    async def get_user_by_email(self, email: str):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT id, email, password_hash, name, organization_id, created_at FROM {self.schema}.users WHERE email = $1",
                email,
            )
            if row is None:
                return None
            return dict(row)

    async def get_user_by_organization_id(self, org_id: str):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT id, email, password_hash, name, organization_id, created_at, stripe_customer_id FROM {self.schema}.users WHERE organization_id = $1",
                org_id,
            )
            if row is None:
                return None
            return dict(row)

    async def update_user_stripe_customer(self, user_id: str, customer_id: str) -> bool:
        """Update stripe_customer_id for a user."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"UPDATE {self.schema}.users SET stripe_customer_id = $1 WHERE id = $2",
                customer_id, user_id,
            )
            count = int(result.split()[-1]) if result else 0
            return count > 0

    async def update_user_organization_id(self, user_id: str, organization_id: str) -> bool:
        """Update organization_id for a user. Returns True if updated."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"UPDATE {self.schema}.users SET organization_id = $1, updated_at = NOW() WHERE id = $2",
                organization_id, user_id,
            )
            count = int(result.split()[-1]) if result else 0
            return count > 0

    async def get_user_by_oauth(self, provider: str, provider_user_id: str):
        """Look up user by OAuth provider + user ID."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT u.id, u.email, u.name, u.organization_id, u.created_at
                FROM {self.schema}.oauth_accounts oa
                JOIN {self.schema}.users u ON u.id = oa.user_id
                WHERE oa.provider = $1 AND oa.provider_user_id = $2
                """,
                provider, provider_user_id,
            )
            if row:
                return dict(row)
            return None

    async def link_oauth_account(self, user_id: str, provider: str, provider_user_id: str):
        """Link an OAuth account to a user."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.oauth_accounts (user_id, provider, provider_user_id, created_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (provider, provider_user_id) DO NOTHING
                """,
                user_id, provider, provider_user_id, datetime.now(timezone.utc),
            )

    # ── Agent ACL (Access Control List) ────────────────────────────────────

    async def set_agent_permission(self, perm: AgentPermission) -> None:
        """Set or update permission for an agent."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.agent_permissions
                    (agent_id, project, scope, allowed_agent_types, can_read, can_write, can_delete, created_at, updated_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8::timestamptz, $9::timestamptz)
                ON CONFLICT (agent_id, project) DO UPDATE SET
                    scope = EXCLUDED.scope,
                    allowed_agent_types = EXCLUDED.allowed_agent_types,
                    can_read = EXCLUDED.can_read,
                    can_write = EXCLUDED.can_write,
                    can_delete = EXCLUDED.can_delete,
                    updated_at = EXCLUDED.updated_at
                """,
                perm.agent_id,
                perm.project,
                perm.scope.value if perm.scope else None,
                json.dumps(perm.allowed_agent_types) if perm.allowed_agent_types is not None else None,
                perm.can_read,
                perm.can_write,
                perm.can_delete,
                perm.created_at,
                perm.updated_at,
            )

    async def get_agent_permission(
        self, agent_id: str, project: Optional[str] = None
    ) -> Optional[AgentPermission]:
        """Get permission for an agent. Returns None if no rule set."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {self.schema}.agent_permissions "
                f"WHERE agent_id = $1 AND ($2::text IS NULL AND project IS NULL OR project = $2)",
                agent_id, project,
            )
            if row is None:
                return None
            raw_types = row.get("allowed_agent_types")
            allowed_types = json.loads(raw_types) if isinstance(raw_types, str) else raw_types if raw_types else None
            scope_val = row.get("scope")
            from ..models import PermissionScope
            return AgentPermission(
                agent_id=row["agent_id"],
                project=row["project"],
                scope=PermissionScope(scope_val) if scope_val else None,
                allowed_agent_types=allowed_types,
                can_read=bool(row["can_read"]),
                can_write=bool(row["can_write"]),
                can_delete=bool(row["can_delete"]),
                created_at=row["created_at"] if isinstance(row["created_at"], datetime) else datetime.fromisoformat(row["created_at"]),
                updated_at=row["updated_at"] if isinstance(row["updated_at"], datetime) else datetime.fromisoformat(row["updated_at"]),
            )

    async def list_agent_permissions(
        self, project: Optional[str] = None
    ) -> list[AgentPermission]:
        """List all agent permission rules, optionally filtered by project."""
        async with self.pool.acquire() as conn:
            if project is not None:
                rows = await conn.fetch(
                    f"SELECT * FROM {self.schema}.agent_permissions "
                    f"WHERE project = $1 ORDER BY agent_id",
                    project,
                )
            else:
                rows = await conn.fetch(
                    f"SELECT * FROM {self.schema}.agent_permissions ORDER BY agent_id"
                )
            return [
                AgentPermission(
                    agent_id=row["agent_id"],
                    project=row["project"],
                    scope=PermissionScope(row["scope"]) if row.get("scope") else None,
                    allowed_agent_types=json.loads(row["allowed_agent_types"]) if isinstance(row.get("allowed_agent_types"), str) else row.get("allowed_agent_types") if row.get("allowed_agent_types") else None,
                    can_read=bool(row["can_read"]),
                    can_write=bool(row["can_write"]),
                    can_delete=bool(row["can_delete"]),
                    created_at=row["created_at"] if isinstance(row["created_at"], datetime) else datetime.fromisoformat(row["created_at"]),
                    updated_at=row["updated_at"] if isinstance(row["updated_at"], datetime) else datetime.fromisoformat(row["updated_at"]),
                )
                for row in rows
            ]

    async def delete_agent_permission(
        self, agent_id: str, project: Optional[str] = None
    ) -> bool:
        """Delete permission rule for an agent. Returns True if deleted."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM {self.schema}.agent_permissions "
                f"WHERE agent_id = $1 AND ($2::text IS NULL AND project IS NULL OR project = $2)",
                agent_id, project,
            )
            # asyncpg's execute returns 'DELETE N' string
            count = int(result.split()[-1]) if result else 0
            return count > 0

    # ── Webhook Subscriptions ────────────────────────────────────────────

    async def store_webhook_subscription(self, sub: dict) -> None:
        """Store or update a webhook subscription."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.webhook_subscriptions
                    (id, url, event_types, secret, project, is_active, created_at)
                VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7::timestamptz)
                ON CONFLICT (id) DO UPDATE SET
                    url = EXCLUDED.url,
                    event_types = EXCLUDED.event_types,
                    secret = EXCLUDED.secret,
                    project = EXCLUDED.project,
                    is_active = EXCLUDED.is_active,
                    created_at = EXCLUDED.created_at
                """,
                sub["id"],
                sub["url"],
                json.dumps(sub["event_types"]),
                sub["secret"],
                sub.get("project"),
                sub.get("is_active", True),
                sub.get("created_at", datetime.now(timezone.utc)),
            )

    async def get_webhook_subscription(self, sub_id: str) -> Optional[dict]:
        """Get a webhook subscription by ID."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {self.schema}.webhook_subscriptions WHERE id = $1",
                sub_id,
            )
            if row is None:
                return None
            return {
                "id": row["id"],
                "url": row["url"],
                "event_types": list(row["event_types"]) if isinstance(row["event_types"], (list, tuple)) else row["event_types"],
                "secret": row["secret"],
                "project": row["project"],
                "is_active": bool(row["is_active"]),
                "created_at": row["created_at"].isoformat() if isinstance(row["created_at"], datetime) else row["created_at"],
            }

    async def list_webhook_subscriptions(
        self, project: Optional[str] = None
    ) -> list[dict]:
        """List webhook subscriptions, optionally filtered by project."""
        async with self.pool.acquire() as conn:
            if project:
                rows = await conn.fetch(
                    f"SELECT * FROM {self.schema}.webhook_subscriptions "
                    f"WHERE project = $1 OR project IS NULL",
                    project,
                )
            else:
                rows = await conn.fetch(
                    f"SELECT * FROM {self.schema}.webhook_subscriptions"
                )
            return [
                {
                    "id": row["id"],
                    "url": row["url"],
                    "event_types": list(row["event_types"]) if isinstance(row["event_types"], (list, tuple)) else row["event_types"],
                    "secret": row["secret"],
                    "project": row["project"],
                    "is_active": bool(row["is_active"]),
                    "created_at": row["created_at"].isoformat() if isinstance(row["created_at"], datetime) else row["created_at"],
                }
                for row in rows
            ]

    async def remove_webhook_subscription(self, sub_id: str) -> bool:
        """Remove a webhook subscription by ID. Returns True if removed."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM {self.schema}.webhook_subscriptions WHERE id = $1",
                sub_id,
            )
            count = int(result.split()[-1]) if result else 0
            return count > 0

    async def store_webhook_delivery(self, delivery: dict) -> None:
        """Record a webhook delivery attempt."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.webhook_deliveries
                    (id, subscription_id, event_type, url, status, status_code, error, attempts, timestamp)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::timestamptz)
                """,
                delivery.get("id", str(uuid4())),
                delivery["subscription_id"],
                delivery["event_type"],
                delivery["url"],
                delivery["status"],
                delivery.get("status_code"),
                delivery.get("error"),
                delivery.get("attempts", 0),
                delivery.get("timestamp", datetime.now(timezone.utc)),
            )

    async def get_webhook_deliveries(
        self, subscription_id: str, limit: int = 50, offset: int = 0
    ) -> tuple[list[dict], int]:
        """Get paginated delivery history for a webhook subscription."""
        async with self.pool.acquire() as conn:
            # Get total count
            total_row = await conn.fetchrow(
                f"SELECT COUNT(*) AS cnt FROM {self.schema}.webhook_deliveries "
                f"WHERE subscription_id = $1",
                subscription_id,
            )
            total = total_row["cnt"] if total_row else 0

            # Get paginated rows
            rows = await conn.fetch(
                f"SELECT * FROM {self.schema}.webhook_deliveries "
                f"WHERE subscription_id = $1 "
                f"ORDER BY timestamp DESC LIMIT $2 OFFSET $3",
                subscription_id, limit, offset,
            )
            deliveries = [
                {
                    "id": row["id"],
                    "subscription_id": row["subscription_id"],
                    "event_type": row["event_type"],
                    "url": row["url"],
                    "status": row["status"],
                    "status_code": row["status_code"],
                    "error": row["error"],
                    "attempts": row["attempts"],
                    "timestamp": row["timestamp"].isoformat() if isinstance(row["timestamp"], datetime) else row["timestamp"],
                }
                for row in rows
            ]
            return deliveries, total

    async def cleanup_old_webhook_deliveries(self, max_age_days: int = 30) -> int:
        """Delete webhook delivery records older than max_age_days.
        Returns the number of records deleted."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM {self.schema}.webhook_deliveries "
                f"WHERE timestamp < NOW() - $1::interval",
                f"{max_age_days} days",
            )
            # asyncpg returns e.g. "DELETE 5" — parse the count
            count = int(result.split()[-1]) if result else 0
            if count:
                logger.info("Cleaned up %d old webhook deliveries (>%d days)", count, max_age_days)
            return count

    # ── Scratchpads (SQL table) ───────────────────────────────────────────────

    async def create_scratchpad(self, pad: Scratchpad) -> Scratchpad:
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.scratchpads
                    (id, session_id, agent_id, project_id, content, contributors, created_at, expires_at, ttl_seconds)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::timestamptz, $8::timestamptz, $9)
                """,
                pad.id,
                pad.session_id,
                pad.agent_id,
                pad.project_id,
                json.dumps(pad.content),
                json.dumps(pad.contributors),
                pad.created_at,
                pad.expires_at,
                pad.ttl_seconds,
            )
        return pad

    async def get_scratchpad(self, id: str) -> Optional[Scratchpad]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {self.schema}.scratchpads WHERE id = $1",
                id,
            )
            if row is None:
                return None
            now = datetime.now(timezone.utc)
            expires_at = row["expires_at"] if isinstance(row["expires_at"], datetime) else datetime.fromisoformat(row["expires_at"])
            if now >= expires_at:
                await conn.execute(
                    f"DELETE FROM {self.schema}.scratchpads WHERE id = $1",
                    id,
                )
                return None
            return self._row_to_scratchpad(row)

    async def update_scratchpad(self, pad: Scratchpad) -> Scratchpad:
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE {self.schema}.scratchpads
                SET content = $1::jsonb,
                    contributors = $2::jsonb,
                    expires_at = $3::timestamptz
                WHERE id = $4
                """,
                json.dumps(pad.content),
                json.dumps(pad.contributors),
                pad.expires_at,
                pad.id,
            )
        return pad

    async def delete_scratchpad(self, id: str) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM {self.schema}.scratchpads WHERE id = $1",
                id,
            )
            count = int(result.split()[-1]) if result else 0
            return count > 0

    async def list_active_scratchpads(self, project_id: str) -> list[Scratchpad]:
        async with self.pool.acquire() as conn:
            now = datetime.now(timezone.utc)
            rows = await conn.fetch(
                f"SELECT * FROM {self.schema}.scratchpads "
                f"WHERE project_id = $1 AND expires_at > $2 "
                f"ORDER BY created_at DESC",
                project_id, now,
            )
            # Clean up expired that may have slipped through
            await conn.execute(
                f"DELETE FROM {self.schema}.scratchpads WHERE expires_at <= $1",
                now,
            )
            return [self._row_to_scratchpad(r) for r in rows]

    async def cleanup_expired_scratchpads(self) -> int:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM {self.schema}.scratchpads WHERE expires_at <= NOW()"
            )
            count = int(result.split()[-1]) if result else 0
            if count:
                logger.info("Cleaned up %d expired scratchpads", count)
            return count

    def _row_to_scratchpad(self, row: asyncpg.Record) -> Scratchpad:
        content = row["content"] if isinstance(row["content"], list) else json.loads(row["content"])
        contributors = row["contributors"] if isinstance(row["contributors"], list) else json.loads(row["contributors"])
        return Scratchpad(
            id=row["id"],
            session_id=row["session_id"],
            agent_id=row["agent_id"],
            project_id=row["project_id"],
            content=content,
            contributors=contributors,
            created_at=row["created_at"] if isinstance(row["created_at"], datetime) else datetime.fromisoformat(row["created_at"]),
            expires_at=row["expires_at"] if isinstance(row["expires_at"], datetime) else datetime.fromisoformat(row["expires_at"]),
            ttl_seconds=row["ttl_seconds"],
        )

    # ── Additional utilities (beyond the ABC) ────────────────────────────────

    async def query_memories_lineage(
        self,
        session_id: str,
        agent_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        keys: Optional[list[str]] = None,
        limit: int = 50,
        offset: int = 0,
        project: Optional[str] = None,
    ) -> list[MemoryEntry]:
        """Gets the session lineage (session + all parents up the chain),
        calls query_memories for each session, merges results deduplicating
        by memory key (child keys override parent keys), and returns merged
        list sorted by created_at DESC.
        """
        lineage = await self.get_session_lineage(session_id)

        # Collect all memories from the lineage — child first so their keys win
        seen_keys: set[str] = set()
        merged: list[MemoryEntry] = []
        for sid in lineage:
            results = await self.query_memories(
                session_id=sid,
                agent_id=agent_id,
                tags=tags,
                keys=keys,
                limit=limit * 2,  # Fetch extra to account for dedup
                offset=offset,
                project=project,
            )
            for entry in results:
                if entry.key not in seen_keys:
                    seen_keys.add(entry.key)
                    merged.append(entry)

        # Sort by created_at DESC and limit
        merged.sort(key=lambda e: e.created_at, reverse=True)
        return merged[:limit]
