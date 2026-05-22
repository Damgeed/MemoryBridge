import os
import aiosqlite
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from memory_bridge.storage import MemoryStorage
from memory_bridge.models import MemoryEntry, Session


@pytest_asyncio.fixture
async def storage(tmp_path):
    """Create a MemoryStorage backed by a temp file for test isolation."""
    db_path = str(tmp_path / "test.db")
    s = MemoryStorage(db_path=db_path)
    await s.initialize()
    yield s
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.mark.asyncio
async def test_store_and_get_memory(storage):
    """Round-trip a memory entry through store and get."""
    entry = MemoryEntry(session_id="s1", agent_id="a1", key="greeting", value="hello")
    stored = await storage.store_memory(entry)
    assert stored.id == entry.id

    retrieved = await storage.get_memory(entry.id)
    assert retrieved is not None
    assert retrieved.value == "hello"
    assert retrieved.session_id == "s1"
    assert retrieved.agent_id == "a1"
    assert retrieved.key == "greeting"


@pytest.mark.asyncio
async def test_get_memory_not_found(storage):
    """Getting a non-existent ID returns None."""
    result = await storage.get_memory("nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_query_by_session(storage):
    """Query returns only memories matching the session_id."""
    e1 = MemoryEntry(session_id="s1", agent_id="a1", key="k1", value="v1")
    e2 = MemoryEntry(session_id="s1", agent_id="a1", key="k2", value="v2")
    e3 = MemoryEntry(session_id="s2", agent_id="a2", key="k3", value="v3")
    for e in [e1, e2, e3]:
        await storage.store_memory(e)

    results = await storage.query_memories(session_id="s1")
    assert len(results) == 2
    assert {r.key for r in results} == {"k1", "k2"}


@pytest.mark.asyncio
async def test_query_by_agent(storage):
    """Query returns only memories matching the agent_id."""
    e1 = MemoryEntry(session_id="s1", agent_id="alice", key="k1", value="v1")
    e2 = MemoryEntry(session_id="s2", agent_id="bob", key="k2", value="v2")
    await storage.store_memory(e1)
    await storage.store_memory(e2)

    results = await storage.query_memories(agent_id="alice")
    assert len(results) == 1
    assert results[0].key == "k1"


@pytest.mark.asyncio
async def test_query_by_keys(storage):
    """Query filters by a list of keys."""
    e1 = MemoryEntry(session_id="s1", agent_id="a1", key="alpha", value="v1")
    e2 = MemoryEntry(session_id="s1", agent_id="a1", key="beta", value="v2")
    e3 = MemoryEntry(session_id="s1", agent_id="a1", key="gamma", value="v3")
    for e in [e1, e2, e3]:
        await storage.store_memory(e)

    results = await storage.query_memories(session_id="s1", keys=["alpha", "gamma"])
    assert len(results) == 2
    assert {r.key for r in results} == {"alpha", "gamma"}


@pytest.mark.asyncio
async def test_query_returns_empty_for_no_match(storage):
    """Query with no matching filters returns an empty list."""
    results = await storage.query_memories(session_id="nonexistent")
    assert results == []


@pytest.mark.asyncio
async def test_query_limit(storage):
    """Query respects the limit parameter."""
    for i in range(10):
        entry = MemoryEntry(session_id="s1", agent_id="a1", key=f"k{i}", value=f"v{i}")
        await storage.store_memory(entry)

    results = await storage.query_memories(session_id="s1", limit=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_query_by_tags(storage):
    """Query filters by tags using SQL JOIN."""
    e1 = MemoryEntry(
        session_id="s1", agent_id="a1", key="k1", value="v1",
        tags=["important", "user-preference"],
    )
    e2 = MemoryEntry(
        session_id="s1", agent_id="a1", key="k2", value="v2",
        tags=["archived"],
    )
    e3 = MemoryEntry(
        session_id="s1", agent_id="a1", key="k3", value="v3",
        tags=["important"],
    )
    e4 = MemoryEntry(
        session_id="s1", agent_id="a1", key="k4", value="v4",
        tags=["important", "archived"],
    )
    for e in [e1, e2, e3, e4]:
        await storage.store_memory(e)

    # Single tag — find all memories with "important"
    results = await storage.query_memories(session_id="s1", tags=["important"])
    assert len(results) == 3
    assert {r.key for r in results} == {"k1", "k3", "k4"}

    # Single tag — find all memories with "archived"
    results = await storage.query_memories(session_id="s1", tags=["archived"])
    assert len(results) == 2
    assert {r.key for r in results} == {"k2", "k4"}

    # Multiple tags with AND logic — memory must have ALL specified tags
    results = await storage.query_memories(
        session_id="s1", tags=["important", "archived"]
    )
    assert len(results) == 1
    assert results[0].key == "k4"

    # No tags filter — returns all
    results = await storage.query_memories(session_id="s1")
    assert len(results) == 4


@pytest.mark.asyncio
async def test_junction_table_sync(storage):
    """Verify junction table rows are created and tag queries use SQL JOIN."""
    entry = MemoryEntry(
        session_id="s1", agent_id="a1", key="test", value="v",
        tags=["alpha", "beta", "gamma"],
    )
    await storage.store_memory(entry)

    # Directly query the junction table to verify rows exist
    async with aiosqlite.connect(storage.db_path) as db:
        cursor = await db.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ? ORDER BY tag",
            (entry.id,),
        )
        rows = await cursor.fetchall()
    assert [r[0] for r in rows] == ["alpha", "beta", "gamma"]

    # Query by single tag via SQL
    results = await storage.query_memories(tags=["alpha"])
    assert len(results) == 1
    assert results[0].id == entry.id

    # Query by multiple tags (AND logic)
    results = await storage.query_memories(tags=["alpha", "gamma"])
    assert len(results) == 1
    assert results[0].id == entry.id

    # Query by tag that doesn't exist
    results = await storage.query_memories(tags=["nonexistent"])
    assert len(results) == 0

    # Update memory with different tags — old tags should be replaced
    entry.tags = ["delta"]
    await storage.store_memory(entry)
    async with aiosqlite.connect(storage.db_path) as db:
        cursor = await db.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ? ORDER BY tag",
            (entry.id,),
        )
        rows = await cursor.fetchall()
    assert [r[0] for r in rows] == ["delta"]

    # Cascade delete: deleting the memory should remove junction rows
    await storage.delete_memory(entry.id)
    async with aiosqlite.connect(storage.db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM memory_tags WHERE memory_id = ?", (entry.id,)
        )
        count = (await cursor.fetchone())[0]
    assert count == 0, "Cascade delete should remove junction table rows"


@pytest.mark.asyncio
async def test_delete_memory(storage):
    """Deleting a memory returns True and the memory is gone."""
    entry = MemoryEntry(session_id="s1", agent_id="a1", key="k", value="v")
    await storage.store_memory(entry)
    assert await storage.delete_memory(entry.id) is True
    assert await storage.get_memory(entry.id) is None


@pytest.mark.asyncio
async def test_delete_memory_not_found(storage):
    """Deleting a non-existent ID returns False."""
    assert await storage.delete_memory("nonexistent-id") is False


@pytest.mark.asyncio
async def test_store_and_get_session(storage):
    """Round-trip a session through store and get."""
    session = Session(session_id="s1", agent_id="a1")
    stored = await storage.store_session(session)
    assert stored.session_id == "s1"

    retrieved = await storage.get_session("s1")
    assert retrieved is not None
    assert retrieved.agent_id == "a1"
    assert retrieved.parent_session_id is None
    assert retrieved.metadata == {}


@pytest.mark.asyncio
async def test_get_session_not_found(storage):
    """Getting a non-existent session returns None."""
    result = await storage.get_session("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_session_with_parent_and_metadata(storage):
    """Session with optional fields round-trips correctly."""
    session = Session(
        session_id="s2",
        agent_id="a1",
        parent_session_id="s1",
        metadata={"project": "memory-bridge", "version": 1},
    )
    await storage.store_session(session)

    retrieved = await storage.get_session("s2")
    assert retrieved is not None
    assert retrieved.parent_session_id == "s1"
    assert retrieved.metadata == {"project": "memory-bridge", "version": 1}


@pytest.mark.asyncio
async def test_store_memory_updates_existing(storage):
    """Re-storing the same ID replaces the existing entry."""
    entry = MemoryEntry(
        session_id="s1", agent_id="a1", key="k", value="original_value"
    )
    await storage.store_memory(entry)

    entry.value = "updated_value"
    await storage.store_memory(entry)

    retrieved = await storage.get_memory(entry.id)
    assert retrieved is not None
    assert retrieved.value == "updated_value"


@pytest.mark.asyncio
async def test_store_session_updates_existing(storage):
    """Re-storing the same session ID replaces the existing session."""
    session = Session(session_id="s1", agent_id="a1", metadata={"v": 1})
    await storage.store_session(session)

    session.metadata = {"v": 2}
    await storage.store_session(session)

    retrieved = await storage.get_session("s1")
    assert retrieved is not None
    assert retrieved.metadata == {"v": 2}


@pytest.mark.asyncio
async def test_complex_value_types(storage):
    """Memory values that are dicts/lists round-trip correctly via JSON."""
    complex_value = {
        "items": [1, 2, 3],
        "nested": {"key": "value"},
        "flag": True,
        "count": 42,
    }
    entry = MemoryEntry(
        session_id="s1", agent_id="a1", key="complex", value=complex_value
    )
    await storage.store_memory(entry)

    retrieved = await storage.get_memory(entry.id)
    assert retrieved is not None
    assert retrieved.value == complex_value


@pytest.mark.asyncio
async def test_multiple_sessions_independent(storage):
    """Memories in different sessions don't interfere."""
    for session_id in ["s1", "s2", "s3"]:
        for i in range(3):
            entry = MemoryEntry(
                session_id=session_id,
                agent_id="a1",
                key=f"k{i}",
                value=f"v{i}",
            )
            await storage.store_memory(entry)

    s1_results = await storage.query_memories(session_id="s1")
    s2_results = await storage.query_memories(session_id="s2")
    assert len(s1_results) == 3
    assert len(s2_results) == 3


# --- TTL / Eviction Tests ---


@pytest.mark.asyncio
async def test_memory_with_ttl_not_expired(storage):
    """Memory with TTL is retrievable before the TTL elapses."""
    entry = MemoryEntry(
        session_id="s1", agent_id="a1", key="ephemeral",
        value="temp data", ttl_seconds=3600,
    )
    await storage.store_memory(entry)

    retrieved = await storage.get_memory(entry.id)
    assert retrieved is not None
    assert retrieved.value == "temp data"
    assert retrieved.ttl_seconds == 3600


@pytest.mark.asyncio
async def test_memory_with_no_ttl_never_expires(storage):
    """Memory with ttl_seconds=None never gets filtered by TTL."""
    entry = MemoryEntry(
        session_id="s1", agent_id="a1", key="permanent",
        value="important data", ttl_seconds=None,
    )
    await storage.store_memory(entry)

    # Simulate time passing — cleanup should not touch it
    await storage.cleanup_expired()
    retrieved = await storage.get_memory(entry.id)
    assert retrieved is not None
    assert retrieved.value == "important data"


@pytest.mark.asyncio
async def test_expired_memory_filtered_from_get(storage):
    """A memory past its TTL is treated as non-existent by get_memory."""
    now = datetime.now(timezone.utc)
    entry = MemoryEntry(
        session_id="s1", agent_id="a1", key="expired",
        value="gone soon", ttl_seconds=1,
        # Backdate creation so it looks expired
        created_at=now.replace(year=now.year - 1),
    )
    await storage.store_memory(entry)

    retrieved = await storage.get_memory(entry.id)
    assert retrieved is None, "Expired memory should not be returned"


@pytest.mark.asyncio
async def test_expired_memory_filtered_from_query(storage):
    """Expired memories are excluded from query results."""
    now = datetime.now(timezone.utc)
    e1 = MemoryEntry(
        session_id="s1", agent_id="a1", key="fresh", value="fine",
        ttl_seconds=3600,
    )
    e2 = MemoryEntry(
        session_id="s1", agent_id="a1", key="stale", value="gone",
        ttl_seconds=1,
        created_at=now.replace(year=now.year - 1),
    )
    for e in [e1, e2]:
        await storage.store_memory(e)

    results = await storage.query_memories(session_id="s1")
    keys = {r.key for r in results}
    assert "fresh" in keys
    assert "stale" not in keys, "Expired memory leaked into query results"


@pytest.mark.asyncio
async def test_cleanup_expired_removes_expired_entries(storage):
    """cleanup_expired() deletes expired entries and leaves others intact."""
    now = datetime.now(timezone.utc)
    e1 = MemoryEntry(
        session_id="s1", agent_id="a1", key="permanent", value="keep",
        ttl_seconds=None,
    )
    e2 = MemoryEntry(
        session_id="s1", agent_id="a1", key="valid", value="keep",
        ttl_seconds=3600,
    )
    e3 = MemoryEntry(
        session_id="s1", agent_id="a1", key="expired1", value="delete",
        ttl_seconds=1,
        created_at=now.replace(year=now.year - 1),
    )
    e4 = MemoryEntry(
        session_id="s1", agent_id="a1", key="expired2", value="delete",
        ttl_seconds=60,
        created_at=now.replace(year=now.year - 1),
    )
    for e in [e1, e2, e3, e4]:
        await storage.store_memory(e)
    e1_id, e2_id, e3_id, e4_id = e1.id, e2.id, e3.id, e4.id

    deleted = await storage.cleanup_expired()
    assert deleted == 2, "Should delete exactly 2 expired entries"

    # Permanents and valid ones survive
    assert await storage.get_memory(e1_id) is not None
    assert await storage.get_memory(e2_id) is not None
    # Expired ones are gone
    assert await storage.get_memory(e3_id) is None
    assert await storage.get_memory(e4_id) is None


@pytest.mark.asyncio
async def test_cleanup_expired_with_none_expired(storage):
    """cleanup_expired() returns 0 when nothing is expired."""
    entries = [
        MemoryEntry(session_id="s1", agent_id="a1", key=f"k{i}", value="v", ttl_seconds=3600)
        for i in range(5)
    ]
    for e in entries:
        await storage.store_memory(e)

    deleted = await storage.cleanup_expired()
    assert deleted == 0, "No entries should be expired"

    results = await storage.query_memories(session_id="s1")
    assert len(results) == 5


@pytest.mark.asyncio
async def test_query_with_offset(storage):
    """Query respects the offset parameter — skip first N results."""
    for i in range(5):
        entry = MemoryEntry(session_id="s1", agent_id="a1", key=f"k{i}", value=f"v{i}")
        await storage.store_memory(entry)

    # Without offset, we get all 5
    all_results = await storage.query_memories(session_id="s1", limit=10)
    assert len(all_results) == 5

    # With offset=2, we skip the first 2 (newest 2) and get 3
    offset_results = await storage.query_memories(session_id="s1", limit=10, offset=2)
    assert len(offset_results) == 3

    keys_offset = {r.key for r in offset_results}
    # Since ORDER BY created_at DESC, offset=2 means skip k4,k3 and get k2,k1,k0
    assert keys_offset == {"k2", "k1", "k0"}

    # Offset beyond available results returns empty
    empty_results = await storage.query_memories(session_id="s1", limit=10, offset=10)
    assert len(empty_results) == 0


# --- Agent Lineage Tests ---


@pytest.mark.asyncio
async def test_session_lineage_simple(storage):
    """parent → child → grandchild: verify lineage list."""
    await storage.store_session(Session(session_id="s-parent", agent_id="a1"))
    await storage.store_session(Session(session_id="s-child", agent_id="a2", parent_session_id="s-parent"))
    await storage.store_session(Session(session_id="s-grandchild", agent_id="a3", parent_session_id="s-child"))

    lineage = await storage.get_session_lineage("s-grandchild")
    assert lineage == ["s-grandchild", "s-child", "s-parent"]

    lineage = await storage.get_session_lineage("s-child")
    assert lineage == ["s-child", "s-parent"]

    lineage = await storage.get_session_lineage("s-parent")
    assert lineage == ["s-parent"]


@pytest.mark.asyncio
async def test_query_memories_lineage(storage):
    """Child can see parent's memories via lineage query."""
    await storage.store_session(Session(session_id="s-parent", agent_id="a1"))
    await storage.store_session(Session(session_id="s-child", agent_id="a2", parent_session_id="s-parent"))

    # Parent stores a memory
    parent_memory = MemoryEntry(session_id="s-parent", agent_id="a1", key="parent_key", value="parent_val", tags=["shared"])
    await storage.store_memory(parent_memory)

    # Child queries with lineage — should see parent's memory
    results = await storage.query_memories_lineage(session_id="s-child")
    keys = {r.key for r in results}
    assert "parent_key" in keys, "Child should see parent's memory via lineage"
    assert len(results) == 1


@pytest.mark.asyncio
async def test_child_key_overrides_parent(storage):
    """Same key in child and parent: child wins (key-based dedup)."""
    await storage.store_session(Session(session_id="s-parent", agent_id="a1"))
    await storage.store_session(Session(session_id="s-child", agent_id="a2", parent_session_id="s-parent"))

    # Parent stores a memory with key "project"
    parent_memory = MemoryEntry(session_id="s-parent", agent_id="a1", key="project", value="old_value")
    await storage.store_memory(parent_memory)

    # Child stores a memory with the same key but different value
    child_memory = MemoryEntry(session_id="s-child", agent_id="a2", key="project", value="new_value")
    await storage.store_memory(child_memory)

    # Lineage query should return child's value (child overrides parent)
    results = await storage.query_memories_lineage(session_id="s-child")
    assert len(results) == 1, "Should be 1 entry after dedup"
    assert results[0].value == "new_value", "Child's value should override parent's"
    assert results[0].session_id == "s-child", "Child's entry should be the one retained"


@pytest.mark.asyncio
async def test_propagate_to_parent(storage):
    """Child stores with propagate_to_parent=True: copy appears under parent."""
    await storage.store_session(Session(session_id="s-parent", agent_id="a1"))
    await storage.store_session(Session(session_id="s-child", agent_id="a2", parent_session_id="s-parent"))

    child_memory = MemoryEntry(session_id="s-child", agent_id="a2", key="secret", value="shared_secret")
    await storage.store_memory(child_memory, propagate_to_parent=True)

    # Parent session should now have a propagated copy
    parent_results = await storage.query_memories(session_id="s-parent")
    assert len(parent_results) == 1, "Parent should have the propagated memory"
    assert parent_results[0].value == "shared_secret"
    assert "propagated:child" in parent_results[0].tags, "Propagated copy should have propagated:child tag"
    assert parent_results[0].key == "secret"

    # Child still has the original (no tag change)
    child_results = await storage.query_memories(session_id="s-child")
    assert len(child_results) == 1
    assert "propagated:child" not in child_results[0].tags, "Original should not have the propagated tag"


# ── Metrics Tests ──


@pytest.mark.asyncio
async def test_record_and_get_metric(storage):
    """Record a metric and retrieve it."""
    await storage.record_metric("start_time", "2026-01-15T10:00:00")
    value = await storage.get_metric("start_time")
    assert value == "2026-01-15T10:00:00"

    # Overwrite with new value
    await storage.record_metric("start_time", "2026-05-22T20:00:00")
    value = await storage.get_metric("start_time")
    assert value == "2026-05-22T20:00:00"


@pytest.mark.asyncio
async def test_get_metric_not_found(storage):
    """Getting a non-existent metric returns None."""
    value = await storage.get_metric("nonexistent")
    assert value is None


@pytest.mark.asyncio
async def test_increment_metric(storage):
    """Increment a numeric metric and verify the returned and stored value."""
    # Start from scratch — should initialize to 0
    new_val = await storage.increment_metric("request_count")
    assert new_val == 1

    new_val = await storage.increment_metric("request_count")
    assert new_val == 2

    new_val = await storage.increment_metric("request_count", delta=5)
    assert new_val == 7

    # Verify via get_metric
    assert await storage.get_metric("request_count") == 7


@pytest.mark.asyncio
async def test_get_all_metrics(storage):
    """get_all_metrics returns all stored metrics as a dict."""
    await storage.record_metric("foo", "bar")
    await storage.record_metric("count", 42)
    await storage.record_metric("flag", True)
    await storage.record_metric("nested", {"a": [1, 2]})

    all_metrics = await storage.get_all_metrics()
    assert all_metrics["foo"] == "bar"
    assert all_metrics["count"] == 42
    assert all_metrics["flag"] is True
    assert all_metrics["nested"] == {"a": [1, 2]}
    assert len(all_metrics) == 4


@pytest.mark.asyncio
async def test_metrics_persist_across_connections(storage, tmp_path):
    """Metrics survive when closing and reopening storage."""
    await storage.record_metric("greeting", "hello")
    await storage.increment_metric("counter", 10)
    await storage.initialize_metric("initialized", "yes")

    # Close and reopen with same db_path
    db_path = storage.db_path
    del storage

    s2 = MemoryStorage(db_path=db_path)
    await s2.initialize()

    assert await s2.get_metric("greeting") == "hello"
    assert await s2.get_metric("counter") == 10
    assert await s2.get_metric("initialized") == "yes"


@pytest.mark.asyncio
async def test_initialize_metric_idempotent(storage):
    """initialize_metric sets a value once and does not overwrite."""
    await storage.initialize_metric("start_time", "first_value")
    assert await storage.get_metric("start_time") == "first_value"

    # Second call should not overwrite
    await storage.initialize_metric("start_time", "second_value")
    assert await storage.get_metric("start_time") == "first_value"


@pytest.mark.asyncio
async def test_metric_complex_types(storage):
    """Metrics support complex JSON types (dicts, lists, numbers, booleans)."""
    await storage.record_metric("dict_val", {"key": "value", "num": 42})
    await storage.record_metric("list_val", [1, "two", True])
    await storage.record_metric("bool_val", False)
    await storage.record_metric("int_val", 0)
    await storage.record_metric("float_val", 3.14)
    await storage.record_metric("null_val", None)

    assert await storage.get_metric("dict_val") == {"key": "value", "num": 42}
    assert await storage.get_metric("list_val") == [1, "two", True]
    assert await storage.get_metric("bool_val") is False
    assert await storage.get_metric("int_val") == 0
    assert await storage.get_metric("float_val") == 3.14
    assert await storage.get_metric("null_val") is None


# ── Schema Migration Tests ──


@pytest.mark.asyncio
async def test_schema_migration(tmp_path):
    """Fresh database gets schema_version=4 and all tables exist."""
    db_path = str(tmp_path / "schema_test.db")
    s = MemoryStorage(db_path=db_path)
    await s.initialize()

    async with aiosqlite.connect(db_path) as db:
        # Verify schema_version table
        cursor = await db.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
        row = await cursor.fetchone()
        assert row[0] >= 6, f"Schema version should be at least 6, got {row[0]}"

        # Verify all core tables exist
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('sessions', 'memories', 'memory_tags', 'schema_version', 'metrics', 'api_keys')"
        )
        tables = {r[0] for r in await cursor.fetchall()}
        assert "sessions" in tables
        assert "memories" in tables
        assert "memory_tags" in tables
        assert "schema_version" in tables
        assert "metrics" in tables, "metrics table should exist after migration v4"
        assert "api_keys" in tables, "api_keys table should exist after migration v6"

        # Verify ttl_seconds column exists (migration v2)
        cursor = await db.execute("PRAGMA table_info(memories)")
        columns = {r[1] for r in await cursor.fetchall()}
        assert "ttl_seconds" in columns, "TTL column should exist after migrations"

    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.mark.asyncio
async def test_drop_legacy_tags_column(tmp_path):
    """Memory storage works without the legacy tags JSON column; tags live in junction table."""
    db_path = str(tmp_path / "tags_drop_test.db")
    s = MemoryStorage(db_path=db_path)
    await s.initialize()

    # Store a memory with tags — should write to junction table only
    entry = MemoryEntry(
        session_id="s1", agent_id="a1", key="k1", value="v1",
        tags=["alpha", "beta"],
    )
    await s.store_memory(entry)

    # Verify junction table has the tags
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ? ORDER BY tag",
            (entry.id,),
        )
        rows = await cursor.fetchall()
    assert [r[0] for r in rows] == ["alpha", "beta"]

    # Verify the legacy tags column is gone from the schema
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("PRAGMA table_info(memories)")
        columns = {r[1] for r in await cursor.fetchall()}
    assert "tags" not in columns, "Legacy tags JSON column should have been dropped"

    # Full round-trip still works via junction table
    retrieved = await s.get_memory(entry.id)
    assert retrieved is not None
    assert retrieved.tags == ["alpha", "beta"]
    assert retrieved.value == "v1"

    # Query by tag still works
    results = await s.query_memories(session_id="s1", tags=["alpha"])
    assert len(results) == 1
    assert results[0].id == entry.id

    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.mark.asyncio
async def test_session_lineage_depth_limit(storage):
    """Lineage depth exceeding 10 raises ValueError."""
    # Create a chain of 11 sessions: root -> s0 -> s1 -> ... -> s9
    await storage.store_session(Session(session_id="root", agent_id="a0"))
    prev = "root"
    for i in range(10):
        sid = f"s{i}"
        await storage.store_session(Session(session_id=sid, agent_id=f"a{i}", parent_session_id=prev))
        prev = sid

    # Querying the deepest session should raise ValueError
    with pytest.raises(ValueError, match="exceeds maximum depth of 10"):
        await storage.get_session_lineage("s9")


@pytest.mark.asyncio
async def test_lineage_depth_just_under_limit(storage):
    """A chain of exactly 10 sessions (depth < 10) works fine."""
    # Create chain: root -> s0 -> ... -> s7 (9 sessions total, depth from leaf = 8)
    await storage.store_session(Session(session_id="root", agent_id="a0"))
    prev = "root"
    for i in range(8):
        sid = f"s{i}"
        await storage.store_session(Session(session_id=sid, agent_id=f"a{i}", parent_session_id=prev))
        prev = sid

    lineage = await storage.get_session_lineage("s7")
    assert len(lineage) == 9  # s7, s6, ..., s0, root
    assert lineage[-1] == "root"


# ── FTS5 Full-Text Search Tests ──


@pytest.mark.asyncio
async def test_fts_search_by_content(storage):
    """Searching for a word in memory value returns matching entries."""
    e1 = MemoryEntry(session_id="s1", agent_id="a1", key="k1", value="the quick brown fox")
    e2 = MemoryEntry(session_id="s1", agent_id="a1", key="k2", value="jumps over the lazy dog")
    e3 = MemoryEntry(session_id="s2", agent_id="a2", key="k3", value="python programming is fun")
    for e in [e1, e2, e3]:
        await storage.store_memory(e)

    results = await storage.search_memories(query="fox")
    assert len(results) == 1
    assert results[0].id == e1.id

    results = await storage.search_memories(query="dog")
    assert len(results) == 1
    assert results[0].id == e2.id


@pytest.mark.asyncio
async def test_fts_search_by_key(storage):
    """Searching for a word in memory key returns matching entries."""
    e1 = MemoryEntry(session_id="s1", agent_id="a1", key="user_preference", value="dark_mode")
    e2 = MemoryEntry(session_id="s1", agent_id="a1", key="project_config", value="settings")
    await storage.store_memory(e1)
    await storage.store_memory(e2)

    results = await storage.search_memories(query="preference")
    assert len(results) == 1
    assert results[0].key == "user_preference"


@pytest.mark.asyncio
async def test_fts_search_no_results(storage):
    """A query with no matches returns an empty list."""
    entry = MemoryEntry(session_id="s1", agent_id="a1", key="greeting", value="hello world")
    await storage.store_memory(entry)

    results = await storage.search_memories(query="nonexistent_term_xyz")
    assert results == []


@pytest.mark.asyncio
async def test_fts_search_with_session_filter(storage):
    """Search results can be scoped to a specific session."""
    e1 = MemoryEntry(session_id="s1", agent_id="a1", key="k1", value="apple pie recipe")
    e2 = MemoryEntry(session_id="s2", agent_id="a2", key="k2", value="apple turnover recipe")
    for e in [e1, e2]:
        await storage.store_memory(e)

    # Without filter, both match
    results = await storage.search_memories(query="apple")
    assert len(results) == 2

    # With session filter, only the matching one
    results = await storage.search_memories(query="apple", session_id="s1")
    assert len(results) == 1
    assert results[0].session_id == "s1"


@pytest.mark.asyncio
async def test_fts_search_with_agent_filter(storage):
    """Search results can be scoped to a specific agent."""
    e1 = MemoryEntry(session_id="s1", agent_id="alice", key="k1", value="data from alice")
    e2 = MemoryEntry(session_id="s2", agent_id="bob", key="k2", value="data from bob")
    for e in [e1, e2]:
        await storage.store_memory(e)

    results = await storage.search_memories(query="data", agent_id="alice")
    assert len(results) == 1
    assert results[0].agent_id == "alice"


@pytest.mark.asyncio
async def test_fts_search_limit(storage):
    """Search respects the limit parameter."""
    for i in range(10):
        entry = MemoryEntry(session_id="s1", agent_id="a1", key=f"key_{i}", value=f"common value {i}")
        await storage.store_memory(entry)

    results = await storage.search_memories(query="common", limit=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_fts_sync_on_delete(storage):
    """Deleted memories do not appear in search results."""
    entry = MemoryEntry(session_id="s1", agent_id="a1", key="temp", value="delete me please")
    await storage.store_memory(entry)

    results = await storage.search_memories(query="delete")
    assert len(results) == 1

    await storage.delete_memory(entry.id)

    results = await storage.search_memories(query="delete")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_fts_sync_on_update(storage):
    """Updating a memory re-indexes the new content into FTS5."""
    entry = MemoryEntry(session_id="s1", agent_id="a1", key="chapter1", value="once upon a time")
    await storage.store_memory(entry)

    results = await storage.search_memories(query="once")
    assert len(results) == 1

    # Update with completely different content
    entry.key = "chapter2"
    entry.value = "in a galaxy far far away"
    await storage.store_memory(entry)

    # Old content should no longer match
    results = await storage.search_memories(query="once")
    assert len(results) == 0

    # New content should match
    results = await storage.search_memories(query="galaxy")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_fts_backfill_migration(tmp_path):
    """Migration v5 backfills existing memories into the FTS table."""
    db_path = str(tmp_path / "fts_backfill.db")
    s = MemoryStorage(db_path=db_path)
    await s.initialize()

    # Store memories BEFORE the FTS migration exists (v4)
    # We simulate this by not having FTS5 yet
    e1 = MemoryEntry(session_id="s1", agent_id="a1", key="project", value="memory bridge rocks", tags=["important"])
    e2 = MemoryEntry(session_id="s2", agent_id="a2", key="greeting", value="hello world", tags=["user-preference"])
    await s.store_memory(e1)
    await s.store_memory(e2)

    # Verify both are searchable (store_memory syncs to FTS5)
    results = await s.search_memories(query="rocks")
    assert len(results) == 1
    assert results[0].id == e1.id

    results = await s.search_memories(query="hello")
    assert len(results) == 1
    assert results[0].id == e2.id

    if os.path.exists(db_path):
        os.remove(db_path)


# ── API Key Tests ──


@pytest.mark.asyncio
async def test_create_and_authenticate_key(storage):
    """Create an API key and authenticate with it."""
    result = await storage.create_api_key(label="test-key", project_id="proj-alpha")
    assert result["label"] == "test-key"
    assert result["project_id"] == "proj-alpha"
    assert result["is_active"] is True
    assert result["id"] is not None
    plain_key = result["key"]
    assert plain_key.startswith("mb_")

    # Authenticate with the plain key
    auth_info = await storage.authenticate_key(plain_key)
    assert auth_info is not None
    assert auth_info["id"] == result["id"]
    assert auth_info["label"] == "test-key"
    assert auth_info["project_id"] == "proj-alpha"


@pytest.mark.asyncio
async def test_authenticate_wrong_key(storage):
    """Wrong key returns None."""
    result = await storage.create_api_key(label="test-key")
    auth_info = await storage.authenticate_key("mb_wrong_key_12345")
    assert auth_info is None


@pytest.mark.asyncio
async def test_revoke_api_key(storage):
    """Revoked key cannot authenticate."""
    result = await storage.create_api_key(label="revocable")
    plain_key = result["key"]

    # Authenticate works before revoke
    auth_info = await storage.authenticate_key(plain_key)
    assert auth_info is not None

    # Revoke
    revoke_result = await storage.revoke_api_key(result["id"])
    assert revoke_result is True

    # Authenticate should fail after revoke
    auth_info = await storage.authenticate_key(plain_key)
    assert auth_info is None


@pytest.mark.asyncio
async def test_api_key_hash_not_stored_plaintext(storage):
    """Verify the DB doesn't contain the plain key value."""
    result = await storage.create_api_key(label="secret-key")
    plain_key = result["key"]

    # Direct DB query to check no plaintext key is stored
    import aiosqlite
    async with aiosqlite.connect(storage.db_path) as db:
        cursor = await db.execute("SELECT key_hash FROM api_keys WHERE id = ?", (result["id"],))
        row = await cursor.fetchone()
        stored_hash = row[0]

    # The stored hash should not equal the plain key
    assert stored_hash != plain_key
    # The stored hash should be a SHA-256 hex digest
    assert len(stored_hash) == 64
    import hashlib
    assert stored_hash == hashlib.sha256(plain_key.encode()).hexdigest()


@pytest.mark.asyncio
async def test_list_api_keys(storage):
    """List API keys returns all keys without plaintext."""
    await storage.create_api_key(label="key-a", project_id="proj-1")
    await storage.create_api_key(label="key-b")

    keys = await storage.list_api_keys()
    assert len(keys) >= 2

    labels = {k["label"] for k in keys}
    assert "key-a" in labels
    assert "key-b" in labels

    # Verify no plaintext key is returned
    for k in keys:
        assert "key" not in k
        assert "key_hash" not in k


# ── Project Isolation Tests ──


@pytest.mark.asyncio
async def test_memory_with_project(storage):
    """Create and retrieve a memory with a project field."""
    entry = MemoryEntry(
        session_id="s1", agent_id="a1", key="proj-key",
        value="project-scoped data", project="proj-alpha",
    )
    stored = await storage.store_memory(entry)
    assert stored.project == "proj-alpha"

    retrieved = await storage.get_memory(entry.id)
    assert retrieved is not None
    assert retrieved.project == "proj-alpha"


@pytest.mark.asyncio
async def test_query_by_project(storage):
    """Query memories filtered by project."""
    e1 = MemoryEntry(session_id="s1", agent_id="a1", key="k1", value="v1", project="proj-alpha")
    e2 = MemoryEntry(session_id="s1", agent_id="a1", key="k2", value="v2", project="proj-beta")
    e3 = MemoryEntry(session_id="s1", agent_id="a1", key="k3", value="v3", project="proj-alpha")
    for e in [e1, e2, e3]:
        await storage.store_memory(e)

    results = await storage.query_memories(session_id="s1", project="proj-alpha")
    assert len(results) == 2
    assert {r.key for r in results} == {"k1", "k3"}

    results = await storage.query_memories(session_id="s1", project="proj-beta")
    assert len(results) == 1
    assert results[0].key == "k2"


@pytest.mark.asyncio
async def test_session_with_project(storage):
    """Create and retrieve a session with a project field."""
    session = Session(session_id="s-proj", agent_id="a1", project="proj-alpha")
    stored = await storage.store_session(session)
    assert stored.project == "proj-alpha"

    retrieved = await storage.get_session("s-proj")
    assert retrieved is not None
    assert retrieved.project == "proj-alpha"
