import aiosqlite
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

from .models import MemoryEntry, Session

logger = logging.getLogger(__name__)


def _row_to_entry(row: aiosqlite.Row) -> MemoryEntry:
    """Convert a SQLite row to a MemoryEntry."""
    # Use bracket access for sqlite3.Row (Python 3.9 compat — no .get())
    try:
        ttl = row["ttl_seconds"]
    except (KeyError, IndexError):
        ttl = None  # Column missing (pre-v0.2 database)
    return MemoryEntry(
        id=row["id"],
        session_id=row["session_id"],
        agent_id=row["agent_id"],
        key=row["key"],
        value=json.loads(row["value"]),
        tags=json.loads(row["tags"]),
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
        """Create tables if they don't exist."""
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
            """)
            # Column migration: add ttl_seconds if missing (pre-v0.2 databases)
            try:
                await db.execute("ALTER TABLE memories ADD COLUMN ttl_seconds INTEGER")
                await db.commit()
            except Exception:
                pass  # Column already exists
            await db.commit()

    async def store_memory(self, entry: MemoryEntry) -> MemoryEntry:
        """Store a memory entry. Replaces if the same ID already exists."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO memories
                   (id, session_id, agent_id, key, value, tags, created_at, updated_at, ttl_seconds)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.id,
                    entry.session_id,
                    entry.agent_id,
                    entry.key,
                    json.dumps(entry.value),
                    json.dumps(entry.tags),
                    entry.created_at.isoformat(),
                    entry.updated_at.isoformat(),
                    entry.ttl_seconds,
                ),
            )
            await db.commit()
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
            entry = _row_to_entry(row)
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
        """Query memories with optional filters. Tags are filtered client-side.
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
                entry = _row_to_entry(row)
                if _is_expired(entry):
                    expired_ids.append(entry.id)
                    continue
                # Client-side tag filtering (tags stored as JSON array)
                if tags:
                    entry_tags = set(entry.tags)
                    if not entry_tags.intersection(tags):
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
            cursor = await db.execute(
                "DELETE FROM memories WHERE id = ?", (memory_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def cleanup_expired(self) -> int:
        """Delete all expired memories. Returns the number of rows deleted."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            # Fetch all memories with a TTL set
            cursor = await db.execute(
                "SELECT * FROM memories WHERE ttl_seconds IS NOT NULL"
            )
            rows = await cursor.fetchall()
            now = datetime.now(timezone.utc)
            expired_ids: list[str] = []
            for row in rows:
                entry = _row_to_entry(row)
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
