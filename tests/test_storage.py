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
