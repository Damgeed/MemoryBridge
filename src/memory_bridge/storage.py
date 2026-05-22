import aiosqlite
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from .models import MemoryEntry, Session

logger = logging.getLogger(__name__)

# ── Schema version manifest ──────────────────────────────────────────────────
#   v1  (0.1.0)  Base tables: sessions, memories (with tags column), memory_tags
#   v2  (0.2.0)  Add ttl_seconds column to memories
#   v3  (0.3.0)  Drop legacy tags column from memories (junction-table only)
_SCHEMA_MIGRATIONS: dict[int, str] = {
    2: "ALTER TABLE memories ADD COLUMN ttl_seconds INTEGER",
    3: "ALTER TABLE memories DROP COLUMN tags",
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
                    metadata TEXT DEFAULT '{}'
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
                await db.execute(ddl)
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
                   (id, session_id, agent_id, key, value, created_at, updated_at, ttl_seconds)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.id,
                    entry.session_id,
                    entry.agent_id,
                    entry.key,
                    json.dumps(entry.value),
                    entry.created_at.isoformat(),
                    entry.updated_at.isoformat(),
                    entry.ttl_seconds,
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

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT * FROM memories WHERE {where_clause} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
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
                await db.commit()

            return results

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory entry by ID. Returns True if a row was deleted."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
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
            await db.commit()
            count = cursor.rowcount
            if count:
                logger.info("Cleaned up %d expired memories", count)
            return count

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
                   (session_id, agent_id, parent_session_id, created_at, metadata)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    session.session_id,
                    session.agent_id,
                    session.parent_session_id,
                    session.created_at.isoformat(),
                    json.dumps(session.metadata),
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
            return Session(
                session_id=row["session_id"],
                agent_id=row["agent_id"],
                parent_session_id=row["parent_session_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                metadata=json.loads(row["metadata"]),
            )

    async def get_session_lineage(self, session_id: str) -> list[str]:
        """Follow parent_session_id chain up recursively.
        Returns ordered list [session_id, parent_id, grandparent_id, ...].
        Max depth of 10 to prevent infinite loops."""
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
        return lineage

    async def query_memories_lineage(
        self,
        session_id: str,
        agent_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        keys: Optional[list[str]] = None,
        limit: int = 50,
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
            )
            for entry in results:
                if entry.key not in seen_keys:
                    seen_keys.add(entry.key)
                    merged.append(entry)

        # Sort by created_at DESC and limit
        merged.sort(key=lambda e: e.created_at, reverse=True)
        return merged[:limit]
