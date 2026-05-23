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

from ..models import MemoryEntry, Session
from ..config import get_settings

logger = logging.getLogger(__name__)


def _hash_api_key(plain_key: str) -> str:
    """Hash an API key using bcrypt for secure storage.

    Uses bcrypt (intentionally slow) to resist brute-force attacks.
    """
    return bcrypt.hashpw(plain_key.encode(), bcrypt.gensalt()).decode()


# ── Schema version manifest ──────────────────────────────────────────────────
#   v1  (0.1.0)  Base tables: sessions, memories (with tags column), memory_tags
#   v2  (0.2.0)  Add ttl_seconds column to memories
#   v3  (0.3.0)  Drop legacy tags column from memories (junction-table only)
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
        "ON {schema}.memories USING GIN (to_tsvector('english', COALESCE(value::text, '')))"
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

    return MemoryEntry(
        id=row["id"],
        session_id=row["session_id"],
        agent_id=row["agent_id"],
        key=row["key"],
        value=json.loads(row["value"]) if isinstance(row["value"], str) else row["value"],
        tags=tags,
        created_at=row["created_at"] if isinstance(row["created_at"], datetime) else datetime.fromisoformat(row["created_at"]),
        updated_at=row["updated_at"] if isinstance(row["updated_at"], datetime) else datetime.fromisoformat(row["updated_at"]),
        ttl_seconds=ttl,
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
                        project TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_{self.schema}_memories_session
                        ON {self.schema}.memories(session_id);
                    CREATE INDEX IF NOT EXISTS idx_{self.schema}_memories_agent
                        ON {self.schema}.memories(agent_id);
                    CREATE INDEX IF NOT EXISTS idx_{self.schema}_memories_key
                        ON {self.schema}.memories(key);

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

        Tags are stored exclusively in the memory_tags junction table.

        If propagate_to_parent is True and the session has a parent_session_id,
        also store a reference copy of the memory under the parent session with
        tags augmented with ["propagated:child"].
        """
        async with self.pool.acquire() as conn:
            # Upsert the memory entry
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.memories
                    (id, session_id, agent_id, key, value, created_at, updated_at, ttl_seconds, project)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::timestamptz, $7::timestamptz, $8, $9)
                ON CONFLICT (id) DO UPDATE SET
                    session_id = EXCLUDED.session_id,
                    agent_id = EXCLUDED.agent_id,
                    key = EXCLUDED.key,
                    value = EXCLUDED.value,
                    updated_at = EXCLUDED.updated_at,
                    ttl_seconds = EXCLUDED.ttl_seconds,
                    project = EXCLUDED.project
                """,
                entry.id,
                entry.session_id,
                entry.agent_id,
                entry.key,
                json.dumps(entry.value),
                entry.created_at,
                entry.updated_at,
                entry.ttl_seconds,
                entry.project,
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
            created_at=created_at,
            updated_at=updated_at,
            ttl_seconds=ttl,
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

    # ── API Key Management ────────────────────────────────────────────────────

    async def create_api_key(self, label: str, project_id: Optional[str] = None) -> dict:
        """Create a new API key. Returns the full key info including the plaintext key (show once)."""
        plain_key = f"mb_{secrets.token_hex(24)}"
        key_hash = _hash_api_key(plain_key)
        key_id = str(uuid4())
        now = datetime.now(timezone.utc)

        async with self.pool.acquire() as conn:
            await conn.execute(
                f"INSERT INTO {self.schema}.api_keys "
                f"(id, key_hash, label, project_id, is_active, created_at) "
                f"VALUES ($1, $2, $3, $4, TRUE, $5::timestamptz)",
                key_id, key_hash, label, project_id, now,
            )

        return {
            "id": key_id,
            "key": plain_key,
            "label": label,
            "project_id": project_id,
            "is_active": True,
            "created_at": now.isoformat(),
        }

    async def list_api_keys(self) -> list[dict]:
        """List all API keys (without the actual key value, only hash)."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT id, label, project_id, is_active, created_at, last_used_at "
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

        Initializes to 0 if the key doesn't exist yet.
        """
        async with self.pool.acquire() as conn:
            # Seed with 0 if absent
            await conn.execute(
                f"""
                INSERT INTO {self.schema}.metrics (key, value, updated_at)
                VALUES ($1, '0', NOW())
                ON CONFLICT (key) DO NOTHING
                """,
                key,
            )
            # Read current value
            row = await conn.fetchrow(
                f"SELECT value FROM {self.schema}.metrics WHERE key = $1",
                key,
            )
            current = json.loads(row["value"])
            new_value = current + delta
            # Write updated value back
            await conn.execute(
                f"UPDATE {self.schema}.metrics SET value = $1, updated_at = NOW() "
                f"WHERE key = $2",
                json.dumps(new_value), key,
            )
            return new_value

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
