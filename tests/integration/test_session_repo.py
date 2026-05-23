"""Contract tests for session operations — run against both SQLite and PostgreSQL backends.

Tests cover session CRUD, lineage traversal, counts, and the session-related
API key and metrics methods defined on the MemoryRepository ABC.
"""

import pytest

from memory_bridge.repository import MemoryRepository
from tests.integration.conftest import make_session, make_memory


# ═══════════════════════════════════════════════════════════════════════════
# Shared test logic — defined once, invoked by both backend test classes
# ═══════════════════════════════════════════════════════════════════════════


async def _test_store_and_get_session(repo: MemoryRepository):
    """Round-trip a session through store and get."""
    session = make_session(session_id="s1", agent_id="a1")
    stored = await repo.store_session(session)
    assert stored.session_id == "s1"

    retrieved = await repo.get_session("s1")
    assert retrieved is not None
    assert retrieved.agent_id == "a1"
    assert retrieved.parent_session_id is None
    assert retrieved.metadata == {}


async def _test_get_session_not_found(repo: MemoryRepository):
    """Getting a non-existent session returns None."""
    result = await repo.get_session("nonexistent")
    assert result is None


async def _test_session_with_parent_and_metadata(repo: MemoryRepository):
    """Session with optional fields round-trips correctly."""
    session = make_session(
        session_id="s2",
        agent_id="a1",
        parent_session_id="s1",
        project="proj-x",
    )
    session.metadata = {"project": "memory-bridge", "version": 1}
    await repo.store_session(session)

    retrieved = await repo.get_session("s2")
    assert retrieved is not None
    assert retrieved.parent_session_id == "s1"
    assert retrieved.metadata == {"project": "memory-bridge", "version": 1}
    assert retrieved.project == "proj-x"


async def _test_session_update_existing(repo: MemoryRepository):
    """Re-storing the same session ID replaces the existing session."""
    session = make_session(session_id="s1", agent_id="a1")
    session.metadata = {"v": 1}
    await repo.store_session(session)

    session.metadata = {"v": 2}
    await repo.store_session(session)

    retrieved = await repo.get_session("s1")
    assert retrieved is not None
    assert retrieved.metadata == {"v": 2}


async def _test_count_sessions(repo: MemoryRepository):
    """count_sessions reflects correct total."""
    assert await repo.count_sessions() == 0
    for i in range(3):
        await repo.store_session(make_session(session_id=f"s{i}", agent_id="a1"))
    assert await repo.count_sessions() == 3


async def _test_session_lineage_simple(repo: MemoryRepository):
    """parent → child → grandchild: verify lineage list."""
    await repo.store_session(make_session(session_id="s-parent", agent_id="a1"))
    await repo.store_session(make_session(session_id="s-child", agent_id="a2", parent_session_id="s-parent"))
    await repo.store_session(make_session(session_id="s-grandchild", agent_id="a3", parent_session_id="s-child"))

    lineage = await repo.get_session_lineage("s-grandchild")
    assert lineage == ["s-grandchild", "s-child", "s-parent"]

    lineage = await repo.get_session_lineage("s-child")
    assert lineage == ["s-child", "s-parent"]

    lineage = await repo.get_session_lineage("s-parent")
    assert lineage == ["s-parent"]


async def _test_session_lineage_no_parent(repo: MemoryRepository):
    """Session with no parent returns list containing only itself."""
    await repo.store_session(make_session(session_id="s-orphan", agent_id="a1"))
    lineage = await repo.get_session_lineage("s-orphan")
    assert lineage == ["s-orphan"]


async def _test_session_lineage_deep_chain(repo: MemoryRepository):
    """Deep lineage chain stops at depth limit (10) and raises ValueError."""
    await repo.store_session(make_session(session_id="s0", agent_id="a0"))
    for i in range(1, 12):
        await repo.store_session(
            make_session(session_id=f"s{i}", agent_id=f"a{i}", parent_session_id=f"s{i-1}")
        )

    with pytest.raises(ValueError, match="exceeds maximum depth"):
        await repo.get_session_lineage("s11")


async def _test_session_lineage_missing_intermediate(repo: MemoryRepository):
    """Lineage includes parent_session_id even if the parent record is missing."""
    await repo.store_session(make_session(session_id="s-child", agent_id="a2", parent_session_id="s-parent"))
    # s-parent does not exist — lineage includes the ID from parent_session_id
    lineage = await repo.get_session_lineage("s-child")
    assert lineage == ["s-child", "s-parent"]


async def _test_session_with_project(repo: MemoryRepository):
    """Sessions with project scope round-trip correctly."""
    session = make_session(session_id="s-proj", agent_id="a1", project="my-project")
    await repo.store_session(session)
    retrieved = await repo.get_session("s-proj")
    assert retrieved is not None
    assert retrieved.project == "my-project"


async def _test_session_metadata_complex(repo: MemoryRepository):
    """Session metadata with complex nested dicts round-trips correctly."""
    session = make_session(session_id="s-complex", agent_id="a1")
    session.metadata = {"nested": {"list": [1, 2, 3], "bool": True}}
    await repo.store_session(session)
    retrieved = await repo.get_session("s-complex")
    assert retrieved is not None
    assert retrieved.metadata == {"nested": {"list": [1, 2, 3], "bool": True}}


# ── API Key contract tests ────────────────────────────────────────────────


async def _test_create_api_key(repo: MemoryRepository):
    """Create an API key returns expected shape with plaintext key."""
    result = await repo.create_api_key(label="test-key")
    assert result["id"] is not None
    assert result["key"].startswith("mb_")
    assert result["label"] == "test-key"
    assert result["is_active"] is True
    assert result["project_id"] is None
    assert result["created_at"] is not None


async def _test_create_api_key_with_project(repo: MemoryRepository):
    """API key with project_id is stored correctly."""
    result = await repo.create_api_key(label="project-key", project_id="proj-a")
    assert result["project_id"] == "proj-a"


async def _test_authenticate_valid_key(repo: MemoryRepository):
    """A valid API key can be authenticated."""
    created = await repo.create_api_key(label="auth-test")
    plain_key = created["key"]
    auth_result = await repo.authenticate_key(plain_key)
    assert auth_result is not None
    assert auth_result["id"] == created["id"]


async def _test_authenticate_wrong_key(repo: MemoryRepository):
    """An invalid API key returns None."""
    result = await repo.authenticate_key("mb_invalid_key_here")
    assert result is None


async def _test_list_api_keys(repo: MemoryRepository):
    """list_api_keys returns all keys (without plaintext)."""
    k1 = await repo.create_api_key(label="key1")
    k2 = await repo.create_api_key(label="key2")
    keys = await repo.list_api_keys()
    ids = {k["id"] for k in keys}
    assert k1["id"] in ids
    assert k2["id"] in ids
    # Plain key should never be in the list
    for k in keys:
        assert "key" not in k or k.get("key") is None


async def _test_revoke_api_key(repo: MemoryRepository):
    """Revoked key returns False from authenticate_key."""
    created = await repo.create_api_key(label="revoke-me")
    assert await repo.revoke_api_key(created["id"]) is True
    auth_result = await repo.authenticate_key(created["key"])
    assert auth_result is None


async def _test_revoke_nonexistent_key(repo: MemoryRepository):
    """Revoking a non-existent key returns False."""
    assert await repo.revoke_api_key("nonexistent-id") is False


# ── Metrics contract tests ────────────────────────────────────────────────


async def _test_record_and_get_metric(repo: MemoryRepository):
    """A metric can be stored and retrieved."""
    await repo.record_metric("test_count", 42)
    value = await repo.get_metric("test_count")
    assert value == 42


async def _test_get_metric_not_found(repo: MemoryRepository):
    """Getting a non-existent metric returns None."""
    value = await repo.get_metric("no_such_metric")
    assert value is None


async def _test_get_all_metrics(repo: MemoryRepository):
    """get_all_metrics returns all stored metrics."""
    await repo.record_metric("alpha", 1)
    await repo.record_metric("beta", "string_val")
    await repo.record_metric("gamma", {"nested": True})
    all_metrics = await repo.get_all_metrics()
    assert all_metrics["alpha"] == 1
    assert all_metrics["beta"] == "string_val"
    assert all_metrics["gamma"] == {"nested": True}


async def _test_increment_metric(repo: MemoryRepository):
    """increment_metric atomically increments and returns new value."""
    val = await repo.increment_metric("counter", delta=5)
    assert val == 5
    val = await repo.increment_metric("counter", delta=1)
    assert val == 6


async def _test_initialize_metric(repo: MemoryRepository):
    """initialize_metric sets only if not already present."""
    await repo.initialize_metric("start_time", "2025-01-01")
    val = await repo.get_metric("start_time")
    assert val == "2025-01-01"
    # Second call should NOT overwrite
    await repo.initialize_metric("start_time", "overwritten")
    val = await repo.get_metric("start_time")
    assert val == "2025-01-01", "initialize_metric should not overwrite existing"


# ═══════════════════════════════════════════════════════════════════════════
# SQLite test class
# ═══════════════════════════════════════════════════════════════════════════


class TestSessionRepoContractSQLite:
    """Contract tests for SQLiteMemoryRepository — session operations."""

    @pytest.mark.asyncio
    async def test_store_and_get_session(self, sqlite_repo):
        await _test_store_and_get_session(sqlite_repo)

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, sqlite_repo):
        await _test_get_session_not_found(sqlite_repo)

    @pytest.mark.asyncio
    async def test_session_with_parent_and_metadata(self, sqlite_repo):
        await _test_session_with_parent_and_metadata(sqlite_repo)

    @pytest.mark.asyncio
    async def test_session_update_existing(self, sqlite_repo):
        await _test_session_update_existing(sqlite_repo)

    @pytest.mark.asyncio
    async def test_count_sessions(self, sqlite_repo):
        await _test_count_sessions(sqlite_repo)

    @pytest.mark.asyncio
    async def test_session_lineage_simple(self, sqlite_repo):
        await _test_session_lineage_simple(sqlite_repo)

    @pytest.mark.asyncio
    async def test_session_lineage_no_parent(self, sqlite_repo):
        await _test_session_lineage_no_parent(sqlite_repo)

    @pytest.mark.asyncio
    async def test_session_lineage_deep_chain(self, sqlite_repo):
        await _test_session_lineage_deep_chain(sqlite_repo)

    @pytest.mark.asyncio
    async def test_session_lineage_missing_intermediate(self, sqlite_repo):
        await _test_session_lineage_missing_intermediate(sqlite_repo)

    @pytest.mark.asyncio
    async def test_session_with_project(self, sqlite_repo):
        await _test_session_with_project(sqlite_repo)

    @pytest.mark.asyncio
    async def test_session_metadata_complex(self, sqlite_repo):
        await _test_session_metadata_complex(sqlite_repo)

    # ── API Key tests ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_api_key(self, sqlite_repo):
        await _test_create_api_key(sqlite_repo)

    @pytest.mark.asyncio
    async def test_create_api_key_with_project(self, sqlite_repo):
        await _test_create_api_key_with_project(sqlite_repo)

    @pytest.mark.asyncio
    async def test_authenticate_valid_key(self, sqlite_repo):
        await _test_authenticate_valid_key(sqlite_repo)

    @pytest.mark.asyncio
    async def test_authenticate_wrong_key(self, sqlite_repo):
        await _test_authenticate_wrong_key(sqlite_repo)

    @pytest.mark.asyncio
    async def test_list_api_keys(self, sqlite_repo):
        await _test_list_api_keys(sqlite_repo)

    @pytest.mark.asyncio
    async def test_revoke_api_key(self, sqlite_repo):
        await _test_revoke_api_key(sqlite_repo)

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_key(self, sqlite_repo):
        await _test_revoke_nonexistent_key(sqlite_repo)

    # ── Metrics tests ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_record_and_get_metric(self, sqlite_repo):
        await _test_record_and_get_metric(sqlite_repo)

    @pytest.mark.asyncio
    async def test_get_metric_not_found(self, sqlite_repo):
        await _test_get_metric_not_found(sqlite_repo)

    @pytest.mark.asyncio
    async def test_get_all_metrics(self, sqlite_repo):
        await _test_get_all_metrics(sqlite_repo)

    @pytest.mark.asyncio
    async def test_increment_metric(self, sqlite_repo):
        await _test_increment_metric(sqlite_repo)

    @pytest.mark.asyncio
    async def test_initialize_metric(self, sqlite_repo):
        await _test_initialize_metric(sqlite_repo)


# ═══════════════════════════════════════════════════════════════════════════
# PostgreSQL test class
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.postgres
class TestSessionRepoContractPostgres:
    """Contract tests for PostgresMemoryRepository — session operations."""

    @pytest.mark.asyncio
    async def test_store_and_get_session(self, postgres_repo):
        await _test_store_and_get_session(postgres_repo)

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, postgres_repo):
        await _test_get_session_not_found(postgres_repo)

    @pytest.mark.asyncio
    async def test_session_with_parent_and_metadata(self, postgres_repo):
        await _test_session_with_parent_and_metadata(postgres_repo)

    @pytest.mark.asyncio
    async def test_session_update_existing(self, postgres_repo):
        await _test_session_update_existing(postgres_repo)

    @pytest.mark.asyncio
    async def test_count_sessions(self, postgres_repo):
        await _test_count_sessions(postgres_repo)

    @pytest.mark.asyncio
    async def test_session_lineage_simple(self, postgres_repo):
        await _test_session_lineage_simple(postgres_repo)

    @pytest.mark.asyncio
    async def test_session_lineage_no_parent(self, postgres_repo):
        await _test_session_lineage_no_parent(postgres_repo)

    @pytest.mark.asyncio
    async def test_session_lineage_deep_chain(self, postgres_repo):
        await _test_session_lineage_deep_chain(postgres_repo)

    @pytest.mark.asyncio
    async def test_session_lineage_missing_intermediate(self, postgres_repo):
        await _test_session_lineage_missing_intermediate(postgres_repo)

    @pytest.mark.asyncio
    async def test_session_with_project(self, postgres_repo):
        await _test_session_with_project(postgres_repo)

    @pytest.mark.asyncio
    async def test_session_metadata_complex(self, postgres_repo):
        await _test_session_metadata_complex(postgres_repo)

    # ── API Key tests ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_api_key(self, postgres_repo):
        await _test_create_api_key(postgres_repo)

    @pytest.mark.asyncio
    async def test_create_api_key_with_project(self, postgres_repo):
        await _test_create_api_key_with_project(postgres_repo)

    @pytest.mark.asyncio
    async def test_authenticate_valid_key(self, postgres_repo):
        await _test_authenticate_valid_key(postgres_repo)

    @pytest.mark.asyncio
    async def test_authenticate_wrong_key(self, postgres_repo):
        await _test_authenticate_wrong_key(postgres_repo)

    @pytest.mark.asyncio
    async def test_list_api_keys(self, postgres_repo):
        await _test_list_api_keys(postgres_repo)

    @pytest.mark.asyncio
    async def test_revoke_api_key(self, postgres_repo):
        await _test_revoke_api_key(postgres_repo)

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_key(self, postgres_repo):
        await _test_revoke_nonexistent_key(postgres_repo)

    # ── Metrics tests ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_record_and_get_metric(self, postgres_repo):
        await _test_record_and_get_metric(postgres_repo)

    @pytest.mark.asyncio
    async def test_get_metric_not_found(self, postgres_repo):
        await _test_get_metric_not_found(postgres_repo)

    @pytest.mark.asyncio
    async def test_get_all_metrics(self, postgres_repo):
        await _test_get_all_metrics(postgres_repo)

    @pytest.mark.asyncio
    async def test_increment_metric(self, postgres_repo):
        await _test_increment_metric(postgres_repo)

    @pytest.mark.asyncio
    async def test_initialize_metric(self, postgres_repo):
        await _test_initialize_metric(postgres_repo)
