"""Contract tests for memory operations — run against both SQLite and PostgreSQL backends.

Each test class inherits from TestMemoryRepoContract which provides shared test
methods. The actual test classes parametrize the repo fixture so every assertion
runs against both backends with zero code duplication.
"""

import pytest
from datetime import datetime, timezone

from memory_bridge.models import MemoryEntry
from memory_bridge.repository import MemoryRepository
from tests.integration.conftest import make_session, make_memory


# ═══════════════════════════════════════════════════════════════════════════
# Shared test logic — defined once, invoked by both backend test classes
# ═══════════════════════════════════════════════════════════════════════════


async def _test_create_and_get(repo: MemoryRepository):
    """Round-trip a memory entry through store_memory and get_memory."""
    entry = make_memory(session_id="s1", agent_id="a1", key="k1", value="hello")
    stored = await repo.store_memory(entry)
    assert stored.id == entry.id

    retrieved = await repo.get_memory(entry.id)
    assert retrieved is not None
    assert retrieved.value == "hello"
    assert retrieved.session_id == "s1"
    assert retrieved.agent_id == "a1"
    assert retrieved.key == "k1"
    assert retrieved.tags == []


async def _test_get_memory_not_found(repo: MemoryRepository):
    """Getting a non-existent ID returns None."""
    result = await repo.get_memory("nonexistent-id")
    assert result is None


async def _test_delete_memory(repo: MemoryRepository):
    """Deleting returns True and the memory is gone."""
    entry = make_memory(session_id="s1", agent_id="a1", key="k", value="v")
    await repo.store_memory(entry)
    assert await repo.delete_memory(entry.id) is True
    assert await repo.get_memory(entry.id) is None


async def _test_delete_memory_not_found(repo: MemoryRepository):
    """Deleting a non-existent ID returns False."""
    assert await repo.delete_memory("nonexistent-id") is False


async def _test_count_memories(repo: MemoryRepository):
    """count_memories reflects the correct total."""
    assert await repo.count_memories() == 0
    await repo.store_memory(make_memory(session_id="s1", agent_id="a1", key="k1"))
    assert await repo.count_memories() == 1
    for i in range(4):
        await repo.store_memory(make_memory(session_id="s1", agent_id="a1", key=f"k{i}"))
    assert await repo.count_memories() == 5


async def _test_store_updates_existing(repo: MemoryRepository):
    """Re-storing the same ID replaces the existing entry."""
    entry = make_memory(session_id="s1", agent_id="a1", key="k", value="original")
    await repo.store_memory(entry)
    entry.value = "updated"
    await repo.store_memory(entry)
    retrieved = await repo.get_memory(entry.id)
    assert retrieved is not None
    assert retrieved.value == "updated"


async def _test_complex_value_types(repo: MemoryRepository):
    """Dict/list/bool values round-trip correctly through JSON serialization."""
    complex_value = {
        "items": [1, 2, 3],
        "nested": {"key": "value"},
        "flag": True,
        "count": 42,
    }
    entry = make_memory(session_id="s1", agent_id="a1", key="complex", value=complex_value)
    await repo.store_memory(entry)
    retrieved = await repo.get_memory(entry.id)
    assert retrieved is not None
    assert retrieved.value == complex_value


async def _test_query_by_session(repo: MemoryRepository):
    """Query returns only memories matching session_id."""
    entries = [
        make_memory(session_id="s1", agent_id="a1", key="k1"),
        make_memory(session_id="s1", agent_id="a1", key="k2"),
        make_memory(session_id="s2", agent_id="a2", key="k3"),
    ]
    for e in entries:
        await repo.store_memory(e)

    results = await repo.query_memories(session_id="s1")
    assert len(results) == 2
    assert {r.key for r in results} == {"k1", "k2"}


async def _test_query_by_agent(repo: MemoryRepository):
    """Query returns only memories matching agent_id."""
    await repo.store_memory(make_memory(session_id="s1", agent_id="alice", key="k1"))
    await repo.store_memory(make_memory(session_id="s2", agent_id="bob", key="k2"))
    results = await repo.query_memories(agent_id="alice")
    assert len(results) == 1
    assert results[0].key == "k1"


async def _test_query_by_keys(repo: MemoryRepository):
    """Query filters by a list of keys."""
    for key in ["alpha", "beta", "gamma"]:
        await repo.store_memory(make_memory(session_id="s1", agent_id="a1", key=key))
    results = await repo.query_memories(session_id="s1", keys=["alpha", "gamma"])
    assert len(results) == 2
    assert {r.key for r in results} == {"alpha", "gamma"}


async def _test_query_empty_result(repo: MemoryRepository):
    """Query with no matching filters returns empty list."""
    results = await repo.query_memories(session_id="nonexistent")
    assert results == []


async def _test_query_limit(repo: MemoryRepository):
    """Query respects the limit parameter."""
    for i in range(10):
        await repo.store_memory(make_memory(session_id="s1", agent_id="a1", key=f"k{i}"))
    results = await repo.query_memories(session_id="s1", limit=3)
    assert len(results) == 3


async def _test_query_offset(repo: MemoryRepository):
    """Query respects the offset parameter — skips first N results."""
    for i in range(5):
        await repo.store_memory(make_memory(session_id="s1", agent_id="a1", key=f"k{i}"))
    all_results = await repo.query_memories(session_id="s1", limit=10)
    assert len(all_results) == 5
    offset_results = await repo.query_memories(session_id="s1", limit=10, offset=2)
    assert len(offset_results) == 3
    empty_results = await repo.query_memories(session_id="s1", limit=10, offset=10)
    assert len(empty_results) == 0


async def _test_query_by_tags(repo: MemoryRepository):
    """Query filters by tags using AND logic via SQL JOIN."""
    e1 = make_memory(session_id="s1", agent_id="a1", key="k1", tags=["important", "user-preference"])
    e2 = make_memory(session_id="s1", agent_id="a1", key="k2", tags=["archived"])
    e3 = make_memory(session_id="s1", agent_id="a1", key="k3", tags=["important"])
    e4 = make_memory(session_id="s1", agent_id="a1", key="k4", tags=["important", "archived"])
    for e in [e1, e2, e3, e4]:
        await repo.store_memory(e)

    # Single tag
    results = await repo.query_memories(session_id="s1", tags=["important"])
    assert len(results) == 3
    assert {r.key for r in results} == {"k1", "k3", "k4"}

    results = await repo.query_memories(session_id="s1", tags=["archived"])
    assert len(results) == 2
    assert {r.key for r in results} == {"k2", "k4"}

    # AND logic — must have ALL tags
    results = await repo.query_memories(session_id="s1", tags=["important", "archived"])
    assert len(results) == 1
    assert results[0].key == "k4"

    # No tags filter returns all
    results = await repo.query_memories(session_id="s1")
    assert len(results) == 4

    # Tag that doesn't exist
    results = await repo.query_memories(session_id="s1", tags=["nonexistent"])
    assert len(results) == 0


async def _test_query_by_project(repo: MemoryRepository):
    """Query filters by project scope (multi-tenant isolation)."""
    await repo.store_memory(make_memory(session_id="s1", agent_id="a1", key="k1", project="proj-a"))
    await repo.store_memory(make_memory(session_id="s1", agent_id="a1", key="k2", project="proj-b"))
    await repo.store_memory(make_memory(session_id="s1", agent_id="a1", key="k3", project=None))

    results = await repo.query_memories(session_id="s1", project="proj-a")
    assert len(results) == 1
    assert results[0].key == "k1"

    results = await repo.query_memories(session_id="s1", project="proj-b")
    assert len(results) == 1
    assert results[0].key == "k2"


async def _test_tags_replace_on_update(repo: MemoryRepository):
    """Re-storing a memory with new tags replaces the old tags."""
    entry = make_memory(session_id="s1", agent_id="a1", key="k", tags=["alpha", "beta"])
    await repo.store_memory(entry)
    entry.tags = ["delta"]
    await repo.store_memory(entry)
    retrieved = await repo.get_memory(entry.id)
    assert retrieved is not None
    assert retrieved.tags == ["delta"]


async def _test_no_tags_on_update(repo: MemoryRepository):
    """Memory stored without tags has empty tag list."""
    entry = make_memory(session_id="s1", agent_id="a1", key="k")
    await repo.store_memory(entry)
    retrieved = await repo.get_memory(entry.id)
    assert retrieved is not None
    assert retrieved.tags == []


async def _test_search_memories(repo: MemoryRepository):
    """Full-text search across memory values."""
    await repo.store_memory(make_memory(session_id="s1", agent_id="a1", key="project", value="Memory Bridge is awesome"))
    await repo.store_memory(make_memory(session_id="s1", agent_id="a1", key="user", value="Alice likes python programming"))
    await repo.store_memory(make_memory(session_id="s2", agent_id="a2", key="note", value="completely unrelated content"))

    results = await repo.search_memories(query="python")
    assert len(results) == 1
    assert results[0].key == "user"

    results = await repo.search_memories(query="Memory")
    assert len(results) == 1
    assert results[0].key == "project"

    results = await repo.search_memories(query="zzzznotfound")
    assert len(results) == 0


async def _test_search_with_session_filter(repo: MemoryRepository):
    """FTS search respects session_id filter."""
    await repo.store_memory(make_memory(session_id="s1", agent_id="a1", key="k1", value="secret data for alice"))
    await repo.store_memory(make_memory(session_id="s2", agent_id="a2", key="k2", value="secret data for bob"))

    results = await repo.search_memories(query="secret", session_id="s1")
    assert len(results) == 1
    assert results[0].session_id == "s1"

    results = await repo.search_memories(query="secret", session_id="s2")
    assert len(results) == 1
    assert results[0].session_id == "s2"


async def _test_search_with_agent_filter(repo: MemoryRepository):
    """FTS search respects agent_id filter."""
    await repo.store_memory(make_memory(session_id="s1", agent_id="alice", key="k1", value="alice's notes"))
    await repo.store_memory(make_memory(session_id="s2", agent_id="bob", key="k2", value="bob's notes"))

    results = await repo.search_memories(query="notes", agent_id="alice")
    assert len(results) == 1
    assert results[0].agent_id == "alice"


async def _test_search_with_project_filter(repo: MemoryRepository):
    """FTS search respects project filter."""
    await repo.store_memory(make_memory(session_id="s1", agent_id="a1", key="k1", value="shared data", project="proj-a"))
    await repo.store_memory(make_memory(session_id="s1", agent_id="a1", key="k2", value="shared data", project="proj-b"))

    results = await repo.search_memories(query="shared", project="proj-a")
    assert len(results) == 1
    assert results[0].project == "proj-a"


async def _test_search_pagination(repo: MemoryRepository):
    """FTS search respects limit and offset."""
    for i in range(5):
        await repo.store_memory(make_memory(session_id="s1", agent_id="a1", key=f"k{i}", value=f"searchable content {i}"))

    results = await repo.search_memories(query="searchable", limit=2)
    assert len(results) == 2

    results = await repo.search_memories(query="searchable", limit=10, offset=3)
    assert len(results) == 2


async def _test_multiple_sessions_independent(repo: MemoryRepository):
    """Memories in different sessions don't interfere."""
    for session_id in ["s1", "s2", "s3"]:
        for i in range(3):
            await repo.store_memory(make_memory(session_id=session_id, agent_id="a1", key=f"k{i}"))

    s1_results = await repo.query_memories(session_id="s1")
    s2_results = await repo.query_memories(session_id="s2")
    assert len(s1_results) == 3
    assert len(s2_results) == 3


async def _test_propagate_to_parent(repo: MemoryRepository):
    """Storing a memory with propagate_to_parent=True copies to parent session."""
    await repo.store_session(make_session(session_id="s-parent", agent_id="a1"))
    await repo.store_session(make_session(session_id="s-child", agent_id="a2", parent_session_id="s-parent"))

    entry = make_memory(session_id="s-child", agent_id="a2", key="child_key", value="child_val", tags=["shared"])
    await repo.store_memory(entry, propagate_to_parent=True)

    # Child should see it
    child_results = await repo.query_memories(session_id="s-child")
    assert len(child_results) == 1
    assert child_results[0].key == "child_key"

    # Parent should also see it with augmented tags
    parent_results = await repo.query_memories(session_id="s-parent")
    assert len(parent_results) == 1
    assert parent_results[0].key == "child_key"
    assert "propagated:child" in parent_results[0].tags


async def _test_propagate_to_parent_no_parent(repo: MemoryRepository):
    """propagate_to_parent is a no-op when session has no parent."""
    await repo.store_session(make_session(session_id="s-orphan", agent_id="a1"))
    entry = make_memory(session_id="s-orphan", agent_id="a1", key="k", value="v")
    await repo.store_memory(entry, propagate_to_parent=True)
    results = await repo.query_memories(session_id="s-orphan")
    assert len(results) == 1


# ── TTL / Expiry tests ────────────────────────────────────────────────────


async def _test_memory_with_ttl_not_expired(repo: MemoryRepository):
    """Memory with TTL is retrievable before the TTL elapses."""
    entry = make_memory(session_id="s1", agent_id="a1", key="ephemeral", value="temp data", ttl_seconds=3600)
    await repo.store_memory(entry)
    retrieved = await repo.get_memory(entry.id)
    assert retrieved is not None
    assert retrieved.value == "temp data"
    assert retrieved.ttl_seconds == 3600


async def _test_memory_no_ttl_never_expires(repo: MemoryRepository):
    """Memory with ttl_seconds=None never gets filtered by TTL."""
    entry = make_memory(session_id="s1", agent_id="a1", key="permanent", value="important", ttl_seconds=None)
    await repo.store_memory(entry)
    await repo.cleanup_expired()
    retrieved = await repo.get_memory(entry.id)
    assert retrieved is not None


async def _test_expired_memory_filtered_from_get(repo: MemoryRepository):
    """A memory past its TTL is treated as non-existent by get_memory."""
    now = datetime.now(timezone.utc)
    entry = make_memory(
        session_id="s1", agent_id="a1", key="expired", value="gone",
        ttl_seconds=1,
    )
    # Backdate creation so it looks expired
    entry.created_at = now.replace(year=now.year - 1)
    await repo.store_memory(entry)
    retrieved = await repo.get_memory(entry.id)
    assert retrieved is None, "Expired memory should not be returned"


async def _test_expired_memory_filtered_from_query(repo: MemoryRepository):
    """Expired memories are excluded from query results."""
    now = datetime.now(timezone.utc)
    e1 = make_memory(session_id="s1", agent_id="a1", key="fresh", value="fine", ttl_seconds=3600)
    e2 = make_memory(session_id="s1", agent_id="a1", key="stale", value="gone", ttl_seconds=1)
    e2.created_at = now.replace(year=now.year - 1)
    for e in [e1, e2]:
        await repo.store_memory(e)
    results = await repo.query_memories(session_id="s1")
    keys = {r.key for r in results}
    assert "fresh" in keys
    assert "stale" not in keys, "Expired memory leaked into query results"


async def _test_cleanup_expired(repo: MemoryRepository):
    """cleanup_expired() deletes expired entries and leaves others intact."""
    now = datetime.now(timezone.utc)
    entries = [
        make_memory(session_id="s1", agent_id="a1", key="perm", ttl_seconds=None),
        make_memory(session_id="s1", agent_id="a1", key="valid", ttl_seconds=3600),
        make_memory(session_id="s1", agent_id="a1", key="exp1", ttl_seconds=1),
        make_memory(session_id="s1", agent_id="a1", key="exp2", ttl_seconds=60),
    ]
    entries[2].created_at = now.replace(year=now.year - 1)
    entries[3].created_at = now.replace(year=now.year - 1)
    for e in entries:
        await repo.store_memory(e)
    ids = [e.id for e in entries]

    deleted = await repo.cleanup_expired()
    assert deleted == 2, "Should delete exactly 2 expired entries"

    assert await repo.get_memory(ids[0]) is not None  # permanent survives
    assert await repo.get_memory(ids[1]) is not None  # valid survives
    assert await repo.get_memory(ids[2]) is None     # expired gone
    assert await repo.get_memory(ids[3]) is None     # expired gone


async def _test_cleanup_expired_none_expired(repo: MemoryRepository):
    """cleanup_expired() returns 0 when nothing is expired."""
    for i in range(5):
        await repo.store_memory(make_memory(session_id="s1", agent_id="a1", key=f"k{i}", ttl_seconds=3600))
    deleted = await repo.cleanup_expired()
    assert deleted == 0


# ── Query_memories_lineage tests (extra method beyond ABC) ────────────────


async def _test_conflict_resolution_same_value(repo: MemoryRepository):
    """Storing a memory with same key+project and same value does NOT create a conflict."""
    m1 = make_memory(session_id="s1", agent_id="a1", key="same", value="value", project="proj-x")
    stored1 = await repo.store_memory(m1)
    assert stored1.conflicts_resolved == 0

    # Store same key+project with identical value
    m2 = make_memory(session_id="s1", agent_id="a1", key="same", value="value", project="proj-x")
    stored2 = await repo.store_memory(m2)
    assert stored2.conflicts_resolved == 0

    # Both should be queryable (same value = not a conflict)
    results = await repo.query_memories(project="proj-x")
    assert len(results) == 2


async def _test_conflict_resolution_different_value(repo: MemoryRepository):
    """Storing a memory with same key+project but different value supersedes the old one."""
    m1 = make_memory(session_id="s1", agent_id="a1", key="conflict", value="old_value", project="proj-x")
    stored1 = await repo.store_memory(m1)
    assert stored1.conflicts_resolved == 0
    old_id = stored1.id

    # Store same key+project with different value
    m2 = make_memory(session_id="s1", agent_id="a1", key="conflict", value="new_value", project="proj-x")
    stored2 = await repo.store_memory(m2)
    assert stored2.conflicts_resolved == 1

    # Only the new one should appear in queries
    results = await repo.query_memories(project="proj-x")
    assert len(results) == 1
    assert results[0].id == stored2.id
    assert results[0].value == "new_value"

    # The old one should be retrievable by ID but marked as superseded
    old_entry = await repo.get_memory(old_id)
    assert old_entry is not None
    assert old_entry.superseded_by == stored2.id


async def _test_conflict_resolution_different_project(repo: MemoryRepository):
    """Same key but different project is NOT a conflict."""
    m1 = make_memory(session_id="s1", agent_id="a1", key="shared", value="val_a", project="proj-a")
    m2 = make_memory(session_id="s1", agent_id="a1", key="shared", value="val_b", project="proj-b")
    stored1 = await repo.store_memory(m1)
    stored2 = await repo.store_memory(m2)
    assert stored1.conflicts_resolved == 0
    assert stored2.conflicts_resolved == 0

    # Both should exist (different projects = isolated)
    results_a = await repo.query_memories(project="proj-a")
    results_b = await repo.query_memories(project="proj-b")
    assert len(results_a) == 1
    assert len(results_b) == 1
    assert results_a[0].value == "val_a"
    assert results_b[0].value == "val_b"


async def _test_conflict_resolution_superseded_not_in_search(repo: MemoryRepository):
    """Superseded memories should not appear in full-text search results."""
    m1 = make_memory(session_id="s1", agent_id="a1", key="search_conflict", value="old_searchable", project="proj-x")
    await repo.store_memory(m1)

    m2 = make_memory(session_id="s1", agent_id="a1", key="search_conflict", value="new_searchable", project="proj-x")
    await repo.store_memory(m2)

    # Search should only find the new one
    results = await repo.search_memories(query="searchable", project="proj-x")
    assert len(results) == 1
    assert results[0].value == "new_searchable"


async def _test_conflict_resolution_multiple_supersedes(repo: MemoryRepository):
    """Multiple writes of the same key+project chain supersede correctly."""
    ids = []
    for i in range(3):
        m = make_memory(session_id="s1", agent_id="a1", key="chain", value=f"v{i}", project="proj-x")
        stored = await repo.store_memory(m)
        ids.append(stored.id)
        if i > 0:
            assert stored.conflicts_resolved == 1, f"Write {i} should resolve a conflict"

    # Only the last one should be queryable
    results = await repo.query_memories(project="proj-x")
    assert len(results) == 1
    assert results[0].id == ids[2]
    assert results[0].value == "v2"

    # The first should be marked as superseded by the second
    first = await repo.get_memory(ids[0])
    assert first is not None
    assert first.superseded_by == ids[1]

    # The second should be marked as superseded by the third
    second = await repo.get_memory(ids[1])
    assert second is not None
    assert second.superseded_by == ids[2]


async def _test_query_memories_lineage(repo: MemoryRepository):
    """Child can see parent's memories via lineage query."""
    await repo.store_session(make_session(session_id="s-parent", agent_id="a1"))
    await repo.store_session(make_session(session_id="s-child", agent_id="a2", parent_session_id="s-parent"))

    parent_memory = make_memory(session_id="s-parent", agent_id="a1", key="parent_key", value="parent_val", tags=["shared"])
    await repo.store_memory(parent_memory)

    child_memory = make_memory(session_id="s-child", agent_id="a2", key="child_key", value="child_val")
    await repo.store_memory(child_memory)

    # Child queries with lineage — should see parent's memory too
    results = await repo.query_memories_lineage(session_id="s-child")
    keys = {r.key for r in results}
    assert "parent_key" in keys
    assert "child_key" in keys


async def _test_query_memories_lineage_child_overrides(repo: MemoryRepository):
    """Child's key overrides parent's key in lineage query."""
    await repo.store_session(make_session(session_id="s-parent", agent_id="a1"))
    await repo.store_session(make_session(session_id="s-child", agent_id="a2", parent_session_id="s-parent"))

    await repo.store_memory(make_memory(session_id="s-parent", agent_id="a1", key="shared", value="parent_value"))
    await repo.store_memory(make_memory(session_id="s-child", agent_id="a2", key="shared", value="child_value"))

    results = await repo.query_memories_lineage(session_id="s-child")
    matching = [r for r in results if r.key == "shared"]
    assert len(matching) == 1, "Should be deduplicated"
    assert matching[0].value == "child_value", "Child value should win"


# ═══════════════════════════════════════════════════════════════════════════
# SQLite test class — runs all shared tests against SQLite
# ═══════════════════════════════════════════════════════════════════════════


class TestMemoryRepoContractSQLite:
    """Contract tests for SQLiteMemoryRepository."""

    @pytest.mark.asyncio
    async def test_create_and_get(self, sqlite_repo):
        await _test_create_and_get(sqlite_repo)

    @pytest.mark.asyncio
    async def test_get_not_found(self, sqlite_repo):
        await _test_get_memory_not_found(sqlite_repo)

    @pytest.mark.asyncio
    async def test_delete(self, sqlite_repo):
        await _test_delete_memory(sqlite_repo)

    @pytest.mark.asyncio
    async def test_delete_not_found(self, sqlite_repo):
        await _test_delete_memory_not_found(sqlite_repo)

    @pytest.mark.asyncio
    async def test_count(self, sqlite_repo):
        await _test_count_memories(sqlite_repo)

    @pytest.mark.asyncio
    async def test_store_updates_existing(self, sqlite_repo):
        await _test_store_updates_existing(sqlite_repo)

    @pytest.mark.asyncio
    async def test_complex_value_types(self, sqlite_repo):
        await _test_complex_value_types(sqlite_repo)

    @pytest.mark.asyncio
    async def test_query_by_session(self, sqlite_repo):
        await _test_query_by_session(sqlite_repo)

    @pytest.mark.asyncio
    async def test_query_by_agent(self, sqlite_repo):
        await _test_query_by_agent(sqlite_repo)

    @pytest.mark.asyncio
    async def test_query_by_keys(self, sqlite_repo):
        await _test_query_by_keys(sqlite_repo)

    @pytest.mark.asyncio
    async def test_query_empty_result(self, sqlite_repo):
        await _test_query_empty_result(sqlite_repo)

    @pytest.mark.asyncio
    async def test_query_limit(self, sqlite_repo):
        await _test_query_limit(sqlite_repo)

    @pytest.mark.asyncio
    async def test_query_offset(self, sqlite_repo):
        await _test_query_offset(sqlite_repo)

    @pytest.mark.asyncio
    async def test_query_by_tags(self, sqlite_repo):
        await _test_query_by_tags(sqlite_repo)

    @pytest.mark.asyncio
    async def test_query_by_project(self, sqlite_repo):
        await _test_query_by_project(sqlite_repo)

    @pytest.mark.asyncio
    async def test_tags_replace_on_update(self, sqlite_repo):
        await _test_tags_replace_on_update(sqlite_repo)

    @pytest.mark.asyncio
    async def test_no_tags_on_update(self, sqlite_repo):
        await _test_no_tags_on_update(sqlite_repo)

    @pytest.mark.asyncio
    async def test_search(self, sqlite_repo):
        await _test_search_memories(sqlite_repo)

    @pytest.mark.asyncio
    async def test_search_with_session_filter(self, sqlite_repo):
        await _test_search_with_session_filter(sqlite_repo)

    @pytest.mark.asyncio
    async def test_search_with_agent_filter(self, sqlite_repo):
        await _test_search_with_agent_filter(sqlite_repo)

    @pytest.mark.asyncio
    async def test_search_with_project_filter(self, sqlite_repo):
        await _test_search_with_project_filter(sqlite_repo)

    @pytest.mark.asyncio
    async def test_search_pagination(self, sqlite_repo):
        await _test_search_pagination(sqlite_repo)

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent(self, sqlite_repo):
        await _test_multiple_sessions_independent(sqlite_repo)

    @pytest.mark.asyncio
    async def test_propagate_to_parent(self, sqlite_repo):
        await _test_propagate_to_parent(sqlite_repo)

    @pytest.mark.asyncio
    async def test_propagate_to_parent_no_parent(self, sqlite_repo):
        await _test_propagate_to_parent_no_parent(sqlite_repo)

    @pytest.mark.asyncio
    async def test_ttl_not_expired(self, sqlite_repo):
        await _test_memory_with_ttl_not_expired(sqlite_repo)

    @pytest.mark.asyncio
    async def test_no_ttl_never_expires(self, sqlite_repo):
        await _test_memory_no_ttl_never_expires(sqlite_repo)

    @pytest.mark.asyncio
    async def test_expired_filtered_from_get(self, sqlite_repo):
        await _test_expired_memory_filtered_from_get(sqlite_repo)

    @pytest.mark.asyncio
    async def test_expired_filtered_from_query(self, sqlite_repo):
        await _test_expired_memory_filtered_from_query(sqlite_repo)

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, sqlite_repo):
        await _test_cleanup_expired(sqlite_repo)

    @pytest.mark.asyncio
    async def test_cleanup_expired_none(self, sqlite_repo):
        await _test_cleanup_expired_none_expired(sqlite_repo)

    @pytest.mark.asyncio
    async def test_query_memories_lineage(self, sqlite_repo):
        await _test_query_memories_lineage(sqlite_repo)

    @pytest.mark.asyncio
    async def test_query_memories_lineage_child_overrides(self, sqlite_repo):
        await _test_query_memories_lineage_child_overrides(sqlite_repo)

    # ── Conflict resolution tests ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_conflict_same_value(self, sqlite_repo):
        await _test_conflict_resolution_same_value(sqlite_repo)

    @pytest.mark.asyncio
    async def test_conflict_different_value(self, sqlite_repo):
        await _test_conflict_resolution_different_value(sqlite_repo)

    @pytest.mark.asyncio
    async def test_conflict_different_project(self, sqlite_repo):
        await _test_conflict_resolution_different_project(sqlite_repo)

    @pytest.mark.asyncio
    async def test_conflict_superseded_not_in_search(self, sqlite_repo):
        await _test_conflict_resolution_superseded_not_in_search(sqlite_repo)

    @pytest.mark.asyncio
    async def test_conflict_multiple_supersedes(self, sqlite_repo):
        await _test_conflict_resolution_multiple_supersedes(sqlite_repo)


# ═══════════════════════════════════════════════════════════════════════════
# PostgreSQL test class — runs the same assertions against Postgres
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.postgres
class TestMemoryRepoContractPostgres:
    """Contract tests for PostgresMemoryRepository (skipped if PG unavailable)."""

    @pytest.mark.asyncio
    async def test_create_and_get(self, postgres_repo):
        await _test_create_and_get(postgres_repo)

    @pytest.mark.asyncio
    async def test_get_not_found(self, postgres_repo):
        await _test_get_memory_not_found(postgres_repo)

    @pytest.mark.asyncio
    async def test_delete(self, postgres_repo):
        await _test_delete_memory(postgres_repo)

    @pytest.mark.asyncio
    async def test_delete_not_found(self, postgres_repo):
        await _test_delete_memory_not_found(postgres_repo)

    @pytest.mark.asyncio
    async def test_count(self, postgres_repo):
        await _test_count_memories(postgres_repo)

    @pytest.mark.asyncio
    async def test_store_updates_existing(self, postgres_repo):
        await _test_store_updates_existing(postgres_repo)

    @pytest.mark.asyncio
    async def test_complex_value_types(self, postgres_repo):
        await _test_complex_value_types(postgres_repo)

    @pytest.mark.asyncio
    async def test_query_by_session(self, postgres_repo):
        await _test_query_by_session(postgres_repo)

    @pytest.mark.asyncio
    async def test_query_by_agent(self, postgres_repo):
        await _test_query_by_agent(postgres_repo)

    @pytest.mark.asyncio
    async def test_query_by_keys(self, postgres_repo):
        await _test_query_by_keys(postgres_repo)

    @pytest.mark.asyncio
    async def test_query_empty_result(self, postgres_repo):
        await _test_query_empty_result(postgres_repo)

    @pytest.mark.asyncio
    async def test_query_limit(self, postgres_repo):
        await _test_query_limit(postgres_repo)

    @pytest.mark.asyncio
    async def test_query_offset(self, postgres_repo):
        await _test_query_offset(postgres_repo)

    @pytest.mark.asyncio
    async def test_query_by_tags(self, postgres_repo):
        await _test_query_by_tags(postgres_repo)

    @pytest.mark.asyncio
    async def test_query_by_project(self, postgres_repo):
        await _test_query_by_project(postgres_repo)

    @pytest.mark.asyncio
    async def test_tags_replace_on_update(self, postgres_repo):
        await _test_tags_replace_on_update(postgres_repo)

    @pytest.mark.asyncio
    async def test_no_tags_on_update(self, postgres_repo):
        await _test_no_tags_on_update(postgres_repo)

    @pytest.mark.asyncio
    async def test_search(self, postgres_repo):
        await _test_search_memories(postgres_repo)

    @pytest.mark.asyncio
    async def test_search_with_session_filter(self, postgres_repo):
        await _test_search_with_session_filter(postgres_repo)

    @pytest.mark.asyncio
    async def test_search_with_agent_filter(self, postgres_repo):
        await _test_search_with_agent_filter(postgres_repo)

    @pytest.mark.asyncio
    async def test_search_with_project_filter(self, postgres_repo):
        await _test_search_with_project_filter(postgres_repo)

    @pytest.mark.asyncio
    async def test_search_pagination(self, postgres_repo):
        await _test_search_pagination(postgres_repo)

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent(self, postgres_repo):
        await _test_multiple_sessions_independent(postgres_repo)

    @pytest.mark.asyncio
    async def test_propagate_to_parent(self, postgres_repo):
        await _test_propagate_to_parent(postgres_repo)

    @pytest.mark.asyncio
    async def test_propagate_to_parent_no_parent(self, postgres_repo):
        await _test_propagate_to_parent_no_parent(postgres_repo)

    @pytest.mark.asyncio
    async def test_ttl_not_expired(self, postgres_repo):
        await _test_memory_with_ttl_not_expired(postgres_repo)

    @pytest.mark.asyncio
    async def test_no_ttl_never_expires(self, postgres_repo):
        await _test_memory_no_ttl_never_expires(postgres_repo)

    @pytest.mark.asyncio
    async def test_expired_filtered_from_get(self, postgres_repo):
        await _test_expired_memory_filtered_from_get(postgres_repo)

    @pytest.mark.asyncio
    async def test_expired_filtered_from_query(self, postgres_repo):
        await _test_expired_memory_filtered_from_query(postgres_repo)

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, postgres_repo):
        await _test_cleanup_expired(postgres_repo)

    @pytest.mark.asyncio
    async def test_cleanup_expired_none(self, postgres_repo):
        await _test_cleanup_expired_none_expired(postgres_repo)

    @pytest.mark.asyncio
    async def test_query_memories_lineage(self, postgres_repo):
        await _test_query_memories_lineage(postgres_repo)

    @pytest.mark.asyncio
    async def test_query_memories_lineage_child_overrides(self, postgres_repo):
        await _test_query_memories_lineage_child_overrides(postgres_repo)

    # ── Conflict resolution tests ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_conflict_same_value(self, postgres_repo):
        await _test_conflict_resolution_same_value(postgres_repo)

    @pytest.mark.asyncio
    async def test_conflict_different_value(self, postgres_repo):
        await _test_conflict_resolution_different_value(postgres_repo)

    @pytest.mark.asyncio
    async def test_conflict_different_project(self, postgres_repo):
        await _test_conflict_resolution_different_project(postgres_repo)

    @pytest.mark.asyncio
    async def test_conflict_superseded_not_in_search(self, postgres_repo):
        await _test_conflict_resolution_superseded_not_in_search(postgres_repo)

    @pytest.mark.asyncio
    async def test_conflict_multiple_supersedes(self, postgres_repo):
        await _test_conflict_resolution_multiple_supersedes(postgres_repo)
