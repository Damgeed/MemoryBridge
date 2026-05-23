"""Fixtures for repository contract tests — SQLite (always) and PostgreSQL (optional)."""

import os
import tempfile
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

import pytest
import pytest_asyncio

from memory_bridge.models import MemoryEntry, Session
from memory_bridge.repository.sqlite_repo import SQLiteMemoryRepository


# ── Shared helpers for creating test data ──────────────────────────────────


def make_session(
    session_id: str = "s-test",
    agent_id: str = "a-test",
    parent_session_id: Optional[str] = None,
    project: Optional[str] = None,
) -> Session:
    """Create a Session with sensible defaults."""
    return Session(
        session_id=session_id,
        agent_id=agent_id,
        parent_session_id=parent_session_id,
        project=project,
    )


def make_memory(
    session_id: str = "s-test",
    agent_id: str = "a-test",
    key: str = "k-test",
    value: object = "v-test",
    tags: Optional[list[str]] = None,
    ttl_seconds: Optional[int] = None,
    project: Optional[str] = None,
) -> MemoryEntry:
    """Create a MemoryEntry with sensible defaults."""
    return MemoryEntry(
        session_id=session_id,
        agent_id=agent_id,
        key=key,
        value=value,
        tags=tags or [],
        ttl_seconds=ttl_seconds,
        project=project,
    )


# ── SQLite fixture ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def sqlite_repo() -> AsyncGenerator[SQLiteMemoryRepository, None]:
    """Create SQLiteMemoryRepository backed by a temp file for test isolation."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    repo = SQLiteMemoryRepository(db_path=path)
    await repo.initialize()
    yield repo
    # Cleanup: remove the temp file
    if os.path.exists(path):
        os.remove(path)


# ── PostgreSQL fixture (optional) ─────────────────────────────────────────


@pytest_asyncio.fixture
async def postgres_repo():
    """Create PostgresMemoryRepository backed by a real PostgreSQL database.

    This fixture is skipped if PostgreSQL is not available.
    Mark tests that use it with @pytest.mark.postgres.
    """
    import asyncpg

    dsn = os.environ.get(
        "MEMORY_BRIDGE_PG_DSN",
        "postgres://mb:mb_dev@localhost:5432/memory_bridge",
    )

    try:
        pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=1)
    except Exception as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")
        return  # unreachable but satisfies type checker

    from memory_bridge.repository.postgres_repo import PostgresMemoryRepository

    schema = f"test_contract_{datetime.now(timezone.utc).strftime('%H%M%S%f')}"
    repo = PostgresMemoryRepository(pool=pool, schema=schema)
    await repo.initialize()

    yield repo

    # Teardown: drop the test schema
    async with pool.acquire() as conn:
        await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    await pool.close()
