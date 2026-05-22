import os
import pytest
import pytest_asyncio
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
    """Query filters by tags (client-side)."""
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
    for e in [e1, e2, e3]:
        await storage.store_memory(e)

    results = await storage.query_memories(session_id="s1", tags=["important"])
    assert len(results) == 2
    assert {r.key for r in results} == {"k1", "k3"}

    results = await storage.query_memories(session_id="s1", tags=["archived"])
    assert len(results) == 1
    assert results[0].key == "k2"

    results = await storage.query_memories(
        session_id="s1", tags=["important", "archived"]
    )
    assert len(results) == 3


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
