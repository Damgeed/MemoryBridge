import aiosqlite
import bcrypt
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional
from pathlib import Path
from uuid import uuid4

from .models import MemoryEntry, Session

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
    2: "ALTER TABLE memories ADD COLUMN ttl_seconds INTEGER",
    3: "ALTER TABLE memories DROP COLUMN tags",
    4: (
        "CREATE TABLE IF NOT EXISTS metrics ("
        "key TEXT PRIMARY KEY, "
        "value TEXT NOT NULL, "
        "updated_at TEXT NOT NULL"
        ")"
    ),
    5: (
        "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(\n"
        "    memory_id UNINDEXED,\n"
        "    key,\n"
        "    value,\n"
        "    tags UNINDEXED,\n"
        "    tokenize='porter unicode61'\n"
        ");\n"
        "INSERT INTO memory_fts (memory_id, key, value, tags)\n"
        "SELECT m.id, m.key, m.value,\n"
        "  COALESCE((SELECT group_concat(mt.tag, ' ') FROM memory_tags mt WHERE mt.memory_id = m.id), '')\n"
        "FROM memories m"
    ),
    6: (
        "CREATE TABLE IF NOT EXISTS api_keys ("
        "  id TEXT PRIMARY KEY, "
        "  key_hash TEXT NOT NULL UNIQUE, "
        "  label TEXT NOT NULL, "
        "  project_id TEXT, "
        "  is_active INTEGER NOT NULL DEFAULT 1, "
        "  created_at TEXT NOT NULL, "
        "  last_used_at TEXT"
        ")"
    ),
    7: "ALTER TABLE memories ADD COLUMN project TEXT",
    8: "ALTER TABLE sessions ADD COLUMN project TEXT",
}
# ──────────────────────────────────────────────────────────────────────────────


async def _row_to_entry(row: aiosqlite.Row, db: aiosqlite.Connection) -> MemoryEntry:
    """Convert a SQLite row to a MemoryEntry, fetching tags from junction table."""
    # Use bracket access for sqlite3.Row (Python 3.9 compat — no .get())
    try:
        ttl = row["ttl_seconds"]
    except (KeyError, IndexError):
        ttl = None  # Column missing (pre-v0.2 database)

    # Fetch tags from the memory_tags junction table
    cursor = await db.execute(
        "SELECT tag FROM memory_tags WHERE memory_id = ?", (row["id"],)
    )
    tag_rows = await cursor.fetchall()
    tags = [r[0] for r in tag_rows]

    try:
        project = row["project"]
    except (KeyError, IndexError):
        project = None

    return MemoryEntry(
        id=row["id"],
        session_id=row["session_id"],
        agent_id=row["agent_id"],
        key=row["key"],
        value=json.loads(row["value"]),
        tags=tags,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
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


class MemoryStorage:
    """SQLite-backed async storage for memory entries and sessions."""

    def __init__(self, db_path: str = "memory_bridge.db"):
        self.db_path = db_path

    async def initialize(self):
        """Create tables if they don't exist and run pending schema migrations."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    parent_session_id TEXT,
                    created_at TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    project TEXT
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    tags TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    ttl_seconds INTEGER,
                    project TEXT,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_memories_session
                    ON memories(session_id);
                CREATE INDEX IF NOT EXISTS idx_memories_agent
                    ON memories(agent_id);
                CREATE INDEX IF NOT EXISTS idx_memories_key
                    ON memories(key);
                CREATE TABLE IF NOT EXISTS memory_tags (
                    memory_id TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    PRIMARY KEY (memory_id, tag),
                    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_memory_tags_tag
                    ON memory_tags(tag);
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metrics (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    key_hash TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL,
                    project_id TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT
                );
            """)
            # Seed version 1 if this is a fresh database (v0.1.0 base tables)
            await db.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (1, datetime('now'))"
            )
            await db.commit()

            # Run sequential migrations
            await self._migrate(db)

    async def _migrate(self, db: aiosqlite.Connection) -> None:
        """Apply pending schema migrations sequentially.

        Each migration is executed in order.  Individual steps that fail
        (e.g. because the column already exists) are logged and skipped
        so that the migration system works on both fresh and upgraded DBs.
        """
        cursor = await db.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
        row = await cursor.fetchone()
        current_version: int = row[0]

        for version, ddl in sorted(_SCHEMA_MIGRATIONS.items()):
            if version <= current_version:
                continue
            try:
                # Use executescript for multi-statement migrations (e.g. v5: FTS5 + backfill)
                await db.executescript(ddl)
                await db.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, datetime('now'))",
                    (version,),
                )
                logger.info("Applied schema migration v%d", version)
            except Exception as exc:
                logger.warning(
                    "Schema migration v%d skipped (%s: %s)", version, type(exc).__name__, exc
                )
        await db.commit()

    async def store_memory(
        self, entry: MemoryEntry, propagate_to_parent: bool = False
    ) -> MemoryEntry:
        """Store a memory entry. Replaces if the same ID already exists.

        Tags are stored exclusively in the memory_tags junction table
        (the legacy tags JSON column was dropped in schema v3).

        If propagate_to_parent is True and the session has a parent_session_id,
        also store a reference copy of the memory under the parent session with
        tags augmented with ["propagated:child"].
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO memories
                   (id, session_id, agent_id, key, value, created_at, updated_at, ttl_seconds, project)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.id,
                    entry.session_id,
                    entry.agent_id,
                    entry.key,
                    json.dumps(entry.value),
                    entry.created_at.isoformat(),
                    entry.updated_at.isoformat(),
                    entry.ttl_seconds,
                    entry.project,
                ),
            )
            # Sync the memory_tags junction table
            await db.execute(
                "DELETE FROM memory_tags WHERE memory_id = ?", (entry.id,)
            )
            for tag in entry.tags:
                await db.execute(
                    "INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                    (entry.id, tag),
                )
            # Sync FTS5 full-text search index
            await db.execute(
                "DELETE FROM memory_fts WHERE memory_id = ?", (entry.id,)
            )
            await db.execute(
                "INSERT INTO memory_fts (memory_id, key, value, tags) VALUES (?, ?, ?, ?)",
                (entry.id, entry.key, json.dumps(entry.value), " ".join(entry.tags)),
            )
            await db.commit()

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
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            entry = await _row_to_entry(row, db)
            if _is_expired(entry):
                # Lazily clean up expired entries on access
                await db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                await db.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
                await db.commit()
                return None
            return entry

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
        Expired memories are filtered out."""
        conditions: list[str] = []
        params: list = []

        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if keys:
            placeholders = ",".join("?" for _ in keys)
            conditions.append(f"key IN ({placeholders})")
            params.extend(keys)
        if tags:
            unique_tags = list(set(tags))
            placeholders = ",".join("?" for _ in unique_tags)
            # Use subquery with GROUP BY/HAVING for AND logic — memory must match ALL specified tags
            conditions.append(
                f"id IN ("
                f"  SELECT memory_id FROM memory_tags "
                f"  WHERE tag IN ({placeholders}) "
                f"  GROUP BY memory_id "
                f"  HAVING COUNT(DISTINCT tag) = ?"
                f")"
            )
            params.extend(unique_tags)
            params.append(len(unique_tags))
        if project:
            conditions.append("project = ?")
            params.append(project)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT * FROM memories WHERE {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            )
            rows = await cursor.fetchall()

            results: list[MemoryEntry] = []
            expired_ids: list[str] = []
            for row in rows:
                entry = await _row_to_entry(row, db)
                if _is_expired(entry):
                    expired_ids.append(entry.id)
                    continue
                results.append(entry)

            # Lazily clean up expired entries
            if expired_ids:
                placeholders = ",".join("?" for _ in expired_ids)
                await db.execute(
                    f"DELETE FROM memories WHERE id IN ({placeholders})", expired_ids
                )
                await db.execute(
                    f"DELETE FROM memory_fts WHERE memory_id IN ({placeholders})", expired_ids
                )
                await db.commit()

            return results

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory entry by ID. Returns True if a row was deleted."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute(
                "DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,)
            )
            cursor = await db.execute(
                "DELETE FROM memories WHERE id = ?", (memory_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def cleanup_expired(self) -> int:
        """Delete all expired memories. Returns the number of rows deleted."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            db.row_factory = aiosqlite.Row
            # Fetch all memories with a TTL set
            cursor = await db.execute(
                "SELECT * FROM memories WHERE ttl_seconds IS NOT NULL"
            )
            rows = await cursor.fetchall()
            now = datetime.now(timezone.utc)
            expired_ids: list[str] = []
            for row in rows:
                entry = await _row_to_entry(row, db)
                elapsed = (now - entry.created_at).total_seconds()
                if elapsed > entry.ttl_seconds:
                    expired_ids.append(entry.id)

            if not expired_ids:
                return 0

            placeholders = ",".join("?" for _ in expired_ids)
            cursor = await db.execute(
                f"DELETE FROM memories WHERE id IN ({placeholders})",
                expired_ids,
            )
            # Also clean up FTS5 index
            await db.execute(
                f"DELETE FROM memory_fts WHERE memory_id IN ({placeholders})",
                expired_ids,
            )
            await db.commit()
            count = cursor.rowcount
            if count:
                logger.info("Cleaned up %d expired memories", count)
            # Record cleanup timestamp in shared metrics
            await self.record_metric("last_cleanup_at", datetime.now(timezone.utc).isoformat())
            return count

    # ── Metrics ───────────────────────────────────────────────────────────────

    async def record_metric(self, key: str, value: Any) -> None:
        """Upsert a metric value (converted to JSON string)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO metrics (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                (key, json.dumps(value)),
            )
            await db.commit()

    async def get_metric(self, key: str) -> Optional[Any]:
        """Get a metric value (parsed from JSON string). Returns None if not set."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT value FROM metrics WHERE key = ?", (key,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return json.loads(row[0])

    async def get_all_metrics(self) -> dict[str, Any]:
        """Get all metrics as a dict mapping key to parsed value."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT key, value FROM metrics")
            rows = await cursor.fetchall()
            return {row[0]: json.loads(row[1]) for row in rows}

    async def increment_metric(self, key: str, delta: int = 1) -> int:
        """Atomically increment a numeric metric. Returns the new value.

        Initializes to 0 if the key doesn't exist yet.
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Seed with 0 if absent
            await db.execute(
                "INSERT OR IGNORE INTO metrics (key, value, updated_at) "
                "VALUES (?, '0', datetime('now'))",
                (key,),
            )
            # Read current value
            cursor = await db.execute(
                "SELECT value FROM metrics WHERE key = ?", (key,)
            )
            row = await cursor.fetchone()
            current = json.loads(row[0])
            new_value = current + delta
            # Write updated value back
            await db.execute(
                "UPDATE metrics SET value = ?, updated_at = datetime('now') "
                "WHERE key = ?",
                (json.dumps(new_value), key),
            )
            await db.commit()
            return new_value

    async def initialize_metric(self, key: str, default_value: Any) -> None:
        """Set a metric only if it doesn't exist yet.

        This is designed for startup counters (start_time, request_count)
        that should be set once and never overwritten.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO metrics (key, value, updated_at) "
                "VALUES (?, ?, datetime('now'))",
                (key, json.dumps(default_value)),
            )
            await db.commit()

    async def count_sessions(self) -> int:
        """Return the total number of sessions in storage."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM sessions")
            row = await cursor.fetchone()
            return row[0]

    async def count_memories(self) -> int:
        """Return the total number of memories in storage."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM memories")
            row = await cursor.fetchone()
            return row[0]

    async def store_session(self, session: Session) -> Session:
        """Store a session record. Replaces if the same session_id exists."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO sessions
                   (session_id, agent_id, parent_session_id, created_at, metadata, project)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    session.session_id,
                    session.agent_id,
                    session.parent_session_id,
                    session.created_at.isoformat(),
                    json.dumps(session.metadata),
                    session.project,
                ),
            )
            await db.commit()
        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Retrieve a session by its ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            try:
                project = row["project"]
            except (KeyError, IndexError):
                project = None
            return Session(
                session_id=row["session_id"],
                agent_id=row["agent_id"],
                parent_session_id=row["parent_session_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                metadata=json.loads(row["metadata"]),
                project=project,
            )

    async def get_session_lineage(self, session_id: str) -> list[str]:
        """Follow parent_session_id chain up recursively.
        Returns ordered list [session_id, parent_id, grandparent_id, ...].
        Raises ValueError if depth exceeds 10 (possible cycle or runaway chain)."""
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
        list sorted by created_at DESC."""
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

    async def search_memories(
        self,
        query: str,
        limit: int = 50,
        offset: int = 0,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        project: Optional[str] = None,
    ) -> list[MemoryEntry]:
        """Full-text search across memory keys, values, and tags using FTS5.

        Returns matching memories ordered by creation time (newest first).
        Filters by session_id and/or agent_id if provided.
        Expired memories are filtered out.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Get matching memory IDs from FTS5
            fts_cursor = await db.execute(
                "SELECT memory_id FROM memory_fts WHERE memory_fts MATCH ?",
                (query,),
            )
            matching_ids = [row[0] for row in await fts_cursor.fetchall()]

            if not matching_ids:
                return []

            # Build query with session/agent filters
            placeholders = ",".join("?" for _ in matching_ids)
            conditions: list[str] = [f"m.id IN ({placeholders})"]
            params: list = list(matching_ids)

            if session_id:
                conditions.append("m.session_id = ?")
                params.append(session_id)
            if agent_id:
                conditions.append("m.agent_id = ?")
                params.append(agent_id)
            if project:
                conditions.append("m.project = ?")
                params.append(project)

            where = " AND ".join(conditions)
            cursor = await db.execute(
                f"SELECT m.* FROM memories m WHERE {where} ORDER BY m.created_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            )
            rows = await cursor.fetchall()

            # Convert to MemoryEntry and filter expired
            results: list[MemoryEntry] = []
            expired_ids: list[str] = []
            for row in rows:
                entry = await _row_to_entry(row, db)
                if _is_expired(entry):
                    expired_ids.append(entry.id)
                    continue
                results.append(entry)

            # Lazily clean up expired entries
            if expired_ids:
                id_placeholders = ",".join("?" for _ in expired_ids)
                await db.execute(
                    f"DELETE FROM memories WHERE id IN ({id_placeholders})", expired_ids
                )
                await db.execute(
                    f"DELETE FROM memory_fts WHERE memory_id IN ({id_placeholders})", expired_ids
                )
                await db.commit()

            return results

    # ── API Key Management ────────────────────────────────────────────────────

    async def create_api_key(self, label: str, project_id: Optional[str] = None) -> dict:
        """Create a new API key. Returns the full key info including the plaintext key (show once)."""
        plain_key = f"mb_{secrets.token_hex(24)}"
        key_hash = _hash_api_key(plain_key)
        key_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO api_keys (id, key_hash, label, project_id, is_active, created_at) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (key_id, key_hash, label, project_id, now),
            )
            await db.commit()

        return {
            "id": key_id,
            "key": plain_key,
            "label": label,
            "project_id": project_id,
            "is_active": True,
            "created_at": now,
        }

    async def list_api_keys(self) -> list[dict]:
        """List all API keys (without the actual key value, only hash)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, label, project_id, is_active, created_at, last_used_at FROM api_keys "
                "ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def revoke_api_key(self, key_id: str) -> bool:
        """Revoke an API key by setting is_active=0."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE api_keys SET is_active = 0 WHERE id = ?", (key_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def authenticate_key(self, plain_key: str) -> Optional[dict]:
        """Authenticate a plaintext API key. Returns key info or None if invalid."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, key_hash, label, project_id, is_active FROM api_keys WHERE is_active = 1",
            )
            rows = await cursor.fetchall()

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
            await db.execute(
                "UPDATE api_keys SET last_used_at = datetime('now') WHERE id = ?",
                (matched_row["id"],),
            )
            await db.commit()

            return {
                "id": matched_row["id"],
                "label": matched_row["label"],
                "project_id": matched_row["project_id"],
            }
