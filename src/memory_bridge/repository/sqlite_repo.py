"""SQLite-backed implementation of MemoryRepository."""

import aiosqlite
import bcrypt
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional
from pathlib import Path
from uuid import uuid4

from ..models import MemoryEntry, Session, Subscription
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
    9: (
        "CREATE INDEX IF NOT EXISTS idx_memories_project_created "
        "ON memories(project, created_at); "
        "CREATE INDEX IF NOT EXISTS idx_memories_session_key "
        "ON memories(session_id, key); "
        "CREATE INDEX IF NOT EXISTS idx_memories_project_agent "
        "ON memories(project, agent_id); "
        "CREATE INDEX IF NOT EXISTS idx_sessions_project_created "
        "ON sessions(project, created_at)"
    ),
    10: (
        "CREATE TABLE IF NOT EXISTS audit_log ("
        "  id TEXT PRIMARY KEY, "
        "  timestamp TEXT NOT NULL, "
        "  actor_type TEXT NOT NULL, "
        "  actor_id TEXT NOT NULL, "
        "  action TEXT NOT NULL, "
        "  resource_type TEXT NOT NULL, "
        "  resource_id TEXT, "
        "  project_id TEXT, "
        "  ip_address TEXT, "
        "  details TEXT DEFAULT '{}', "
        "  previous_hash TEXT, "
        "  hash TEXT"
        "); "
        "CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp DESC); "
        "CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log(actor_type, actor_id); "
        "CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action); "
        "CREATE INDEX IF NOT EXISTS idx_audit_log_project ON audit_log(project_id)"
    ),
    11: (
        "CREATE TABLE IF NOT EXISTS subscriptions ("
        "  id TEXT PRIMARY KEY, "
        "  organization_id TEXT NOT NULL UNIQUE, "
        "  stripe_customer_id TEXT DEFAULT '', "
        "  tier TEXT NOT NULL DEFAULT 'free', "
        "  status TEXT NOT NULL DEFAULT 'active', "
        "  current_period_start TEXT, "
        "  current_period_end TEXT, "
        "  created_at TEXT NOT NULL, "
        "  updated_at TEXT NOT NULL"
        ")"
    ),
    12: (
        "CREATE TABLE IF NOT EXISTS oauth_accounts ("
        "  id TEXT PRIMARY KEY, "
        "  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
        "  provider TEXT NOT NULL, "
        "  provider_user_id TEXT NOT NULL, "
        "  created_at TEXT NOT NULL, "
        "  UNIQUE(provider, provider_user_id)"
        ")"
    ),
    13: "ALTER TABLE users ADD COLUMN stripe_customer_id TEXT NOT NULL DEFAULT ''",
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


from ..repository import MemoryRepository


class SQLiteMemoryRepository(MemoryRepository):
    """SQLite-backed implementation of the MemoryRepository interface."""

    def __init__(self, db_path: Optional[str] = None):
        settings = get_settings()
        self.db_path = db_path or settings.database_url

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
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    organization_id TEXT,
                    role TEXT NOT NULL DEFAULT 'member',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    stripe_customer_id TEXT NOT NULL DEFAULT ''
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

    async def search_memories(
        self,
        query: str,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
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

    async def search_memories_semantic(
        self,
        query_vector: list[float],
        project: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryEntry]:
        """Semantic search — falls back to full-text search for SQLite.

        SQLite does not have pgvector, so we perform a brute-force cosine
        similarity search in Python over all memories in the project scope.
        """
        # Fetch all memories (with optional project filter)
        all_memories = await self.query_memories(
            project=project,
            limit=10000,
            offset=0,
        )

        if not all_memories or not query_vector:
            return []

        # Compute cosine similarity between query vector and each memory
        # (using a local TF-IDF-like embedding computed from value text)
        scored = []
        for mem in all_memories:
            value_text = str(mem.value) if not isinstance(mem.value, str) else mem.value
            # Generate a simple local embedding from the value text
            mem_vec = self._local_embed(value_text)
            sim = self._cosine_similarity(query_vector, mem_vec)
            scored.append((sim, mem))

        # Sort by similarity descending
        scored.sort(key=lambda x: -x[0])

        # Apply pagination
        paginated = scored[offset:offset + limit]
        return [mem for _, mem in paginated]

    @staticmethod
    def _local_embed(text: str) -> list[float]:
        """Simple character-level embedding for local cosine comparison."""
        chars = set(text.lower())
        all_chars = "abcdefghijklmnopqrstuvwxyz0123456789 ._-"
        return [1.0 if c in chars else 0.0 for c in all_chars]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

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

    async def count_sessions(self) -> int:
        """Return the total number of sessions in storage."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM sessions")
            row = await cursor.fetchone()
            return row[0]

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

        Uses atomic UPSERT to eliminate read-then-write race conditions
        that can drop increments under concurrent load.
        Initializes to 0 if the key doesn't exist yet.
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Seed with 0 if absent (no-op on conflict)
            await db.execute(
                "INSERT OR IGNORE INTO metrics (key, value, updated_at) "
                "VALUES (?, '0', datetime('now'))",
                (key,),
            )
            # Atomic increment: value = CAST(COALESCE(NULLIF(value, ''), '0') AS REAL) + delta
            await db.execute(
                "UPDATE metrics SET "
                "value = CAST(COALESCE(NULLIF(value, ''), '0') AS REAL) + ?, "
                "updated_at = datetime('now') "
                "WHERE key = ?",
                (delta, key),
            )
            cursor = await db.execute(
                "SELECT value FROM metrics WHERE key = ?", (key,)
            )
            row = await cursor.fetchone()
            await db.commit()
            return float(row[0]) if row else delta

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
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO audit_log
                   (id, timestamp, actor_type, actor_id, action,
                    resource_type, resource_id, project_id, ip_address,
                    details, previous_hash, hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    id, timestamp, actor_type, actor_id, action,
                    resource_type, resource_id, project_id, ip_address,
                    json.dumps(details), previous_hash, hash,
                ),
            )
            await db.commit()

    async def get_last_audit_hash(self) -> Optional[str]:
        """Get the hash of the most recent audit entry."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT hash FROM audit_log ORDER BY timestamp DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def get_all_audit_entries(self) -> list[dict]:
        """Return all audit entries ordered by timestamp ascending."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM audit_log ORDER BY timestamp ASC"
            )
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                entry = dict(row)
                if isinstance(entry.get("details"), str):
                    import json
                    try:
                        entry["details"] = json.loads(entry["details"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                results.append(entry)
            return results

    # ── Subscription Management ──────────────────────────────────────────────

    async def store_subscription(self, sub: Subscription) -> Subscription:
        """Store a subscription. Replaces if the same id exists."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO subscriptions
                   (id, organization_id, stripe_customer_id, tier, status,
                    current_period_start, current_period_end, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sub.id,
                    sub.organization_id,
                    sub.stripe_customer_id,
                    sub.tier,
                    sub.status,
                    sub.current_period_start.isoformat() if sub.current_period_start else None,
                    sub.current_period_end.isoformat() if sub.current_period_end else None,
                    sub.created_at.isoformat(),
                    sub.updated_at.isoformat(),
                ),
            )
            await db.commit()
        return sub

    async def get_subscription_by_org(self, organization_id: str) -> Optional[Subscription]:
        """Get subscription by organization ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM subscriptions WHERE organization_id = ?",
                (organization_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return Subscription(
                id=row["id"] or "",
                organization_id=row["organization_id"],
                stripe_customer_id=row["stripe_customer_id"] or "",
                tier=row["tier"] or "free",
                status=row["status"] or "active",
                current_period_start=datetime.fromisoformat(row["current_period_start"]) if row["current_period_start"] else None,
                current_period_end=datetime.fromisoformat(row["current_period_end"]) if row["current_period_end"] else None,
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    async def get_subscription_by_stripe_customer(self, customer_id: str) -> Optional[Subscription]:
        """Get subscription by Stripe customer ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM subscriptions WHERE stripe_customer_id = ?",
                (customer_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return Subscription(
                id=row["id"] or "",
                organization_id=row["organization_id"],
                stripe_customer_id=row["stripe_customer_id"] or "",
                tier=row["tier"] or "free",
                status=row["status"] or "active",
                current_period_start=datetime.fromisoformat(row["current_period_start"]) if row["current_period_start"] else None,
                current_period_end=datetime.fromisoformat(row["current_period_end"]) if row["current_period_end"] else None,
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    async def update_subscription_tier(self, sub_id: str, tier: str) -> Optional[Subscription]:
        """Update the tier of a subscription by Stripe subscription ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            now = datetime.now(timezone.utc)
            cursor = await db.execute(
                "UPDATE subscriptions SET tier = ?, updated_at = ? WHERE id = ?",
                (tier, now.isoformat(), sub_id),
            )
            await db.commit()
            if cursor.rowcount == 0:
                return None
            cursor = await db.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            return Subscription(
                id=row["id"] or "",
                organization_id=row["organization_id"],
                stripe_customer_id=row["stripe_customer_id"] or "",
                tier=row["tier"] or "free",
                status=row["status"] or "active",
                current_period_start=datetime.fromisoformat(row["current_period_start"]) if row["current_period_start"] else None,
                current_period_end=datetime.fromisoformat(row["current_period_end"]) if row["current_period_end"] else None,
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    # ── User Management ───────────────────────────────────────────────────────

    async def create_user(self, user) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """INSERT INTO users
                    (id, email, password_hash, name, organization_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    name = excluded.name,
                    updated_at = excluded.updated_at
                RETURNING id""",
                (user.id, user.email, user.password_hash, user.name,
                 user.organization_id, user.created_at.isoformat(), user.updated_at.isoformat()),
            )
            row = await cursor.fetchone()
            user_id = row[0] if row else user.id
            await db.commit()
            return {"id": user_id, "email": user.email, "name": user.name, "organization_id": user.organization_id}

    async def get_user_by_email(self, email: str):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, email, password_hash, name, organization_id, created_at FROM users WHERE email = ?",
                (email,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return dict(row)

    async def get_user_by_organization_id(self, org_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, email, password_hash, name, organization_id, created_at, stripe_customer_id FROM users WHERE organization_id = ?",
                (org_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return dict(row)

    async def get_user_by_oauth(self, provider: str, provider_user_id: str):
        """Look up user by OAuth provider + user ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT u.id, u.email, u.name, u.organization_id, u.created_at
                FROM oauth_accounts oa
                JOIN users u ON u.id = oa.user_id
                WHERE oa.provider = ? AND oa.provider_user_id = ?
                """,
                (provider, provider_user_id),
            )
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

    async def link_oauth_account(self, user_id: str, provider: str, provider_user_id: str):
        """Link an OAuth account to a user."""
        from datetime import datetime, timezone
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO oauth_accounts (user_id, provider, provider_user_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, provider, provider_user_id, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

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
