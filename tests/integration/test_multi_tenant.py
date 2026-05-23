"""Multi-tenant integration tests verifying data isolation between projects."""

import tempfile
import os

import pytest

from memory_bridge.models import MemoryEntry, Session
from memory_bridge.repository.sqlite_repo import SQLiteMemoryRepository
from memory_bridge.services.memory_service import MemoryService
from memory_bridge.services.session_service import SessionService
from memory_bridge.models import MemoryCreate


@pytest.fixture
async def repo():
    db_path = tempfile.mktemp(suffix=".db")
    r = SQLiteMemoryRepository(db_path=db_path)
    await r.initialize()
    yield r
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
async def service(repo):
    return MemoryService(repo=repo)


@pytest.mark.asyncio
async def test_project_isolation(repo):
    """Data stored under different projects should not leak."""
    entry_a = MemoryEntry(session_id="s1", agent_id="a1", key="secret_a", value="aaa", project="proj-alpha")
    await repo.store_memory(entry_a)

    entry_b = MemoryEntry(session_id="s1", agent_id="a2", key="secret_b", value="bbb", project="proj-beta")
    await repo.store_memory(entry_b)

    results = await repo.query_memories(session_id="s1", project="proj-alpha")
    assert len(results) == 1
    assert results[0].key == "secret_a"

    results = await repo.query_memories(session_id="s1", project="proj-beta")
    assert len(results) == 1
    assert results[0].key == "secret_b"


@pytest.mark.asyncio
async def test_project_isolation_no_leak(repo):
    """Querying without project filter should respect explicit scope."""
    entry_a = MemoryEntry(session_id="s1", agent_id="a1", key="k1", value="v1", project="proj-alpha")
    entry_b = MemoryEntry(session_id="s1", agent_id="a2", key="k2", value="v2", project="proj-beta")
    await repo.store_memory(entry_a)
    await repo.store_memory(entry_b)

    # With project filter, only return that project's data
    results = await repo.query_memories(session_id="s1", project="proj-alpha")
    assert len(results) == 1
    assert results[0].key == "k1"

    # Same session, other project
    results = await repo.query_memories(session_id="s1", project="proj-beta")
    assert len(results) == 1
    assert results[0].key == "k2"


@pytest.mark.asyncio
async def test_service_layer_project_enforcement(service):
    """MemoryService should enforce project scope."""
    payload_a = MemoryCreate(session_id="s1", agent_id="a1", key="k1", value="v1", project="proj-alpha")
    await service.create_memory(payload_a)

    payload_b = MemoryCreate(session_id="s1", agent_id="a2", key="k2", value="v2", project="proj-beta")
    await service.create_memory(payload_b)

    results = await service.query_memories(session_id="s1", project="proj-alpha")
    assert len(results) == 1
    assert results[0].key == "k1"

    results = await service.query_memories(session_id="s1", project="proj-beta")
    assert len(results) == 1
    assert results[0].key == "k2"


@pytest.mark.asyncio
async def test_session_project_scoping(repo):
    """Sessions should be scoped to projects."""
    session_a = Session(session_id="s-a", agent_id="a1", project="proj-alpha")
    session_b = Session(session_id="s-b", agent_id="a2", project="proj-beta")
    await repo.store_session(session_a)
    await repo.store_session(session_b)

    assert await repo.get_session("s-a") is not None
    assert await repo.get_session("s-b") is not None


@pytest.mark.asyncio
async def test_tags_across_projects(repo):
    """Tag queries should be project-scoped."""
    entry_a = MemoryEntry(session_id="s1", agent_id="a1", key="k1", value="v1", tags=["important"], project="proj-alpha")
    entry_b = MemoryEntry(session_id="s2", agent_id="a2", key="k2", value="v2", tags=["important"], project="proj-beta")
    await repo.store_memory(entry_a)
    await repo.store_memory(entry_b)

    results = await repo.query_memories(tags=["important"], project="proj-alpha")
    assert len(results) == 1
    assert results[0].key == "k1"

    results = await repo.query_memories(tags=["important"], project="proj-beta")
    assert len(results) == 1
    assert results[0].key == "k2"


@pytest.mark.asyncio
async def test_project_not_leaked_on_delete(repo):
    """Deleting a memory in one project shouldn't affect another."""
    entry_a = MemoryEntry(session_id="s1", agent_id="a1", key="k1", value="v1", project="proj-alpha")
    entry_b = MemoryEntry(session_id="s1", agent_id="a1", key="k1", value="v2", project="proj-beta")
    await repo.store_memory(entry_a)
    await repo.store_memory(entry_b)

    await repo.delete_memory(entry_a.id)

    results = await repo.query_memories(session_id="s1", project="proj-beta")
    assert len(results) == 1
    assert results[0].value == "v2"

    results = await repo.query_memories(session_id="s1", project="proj-alpha")
    assert len(results) == 0


@pytest.mark.asyncio
async def test_tenant_schema_naming(repo):
    """Tenant schema name generation should be deterministic."""
    from memory_bridge.jobs.tenant import generate_tenant_schema_name
    assert generate_tenant_schema_name("proj-abc-123") == "tenant_proj_abc_123"
    assert generate_tenant_schema_name("simple") == "tenant_simple"
    assert generate_tenant_schema_name("proj_id") == "tenant_proj_id"


@pytest.mark.asyncio
async def test_project_filter_on_search(repo):
    """Full-text search should respect project scope."""
    entry_a = MemoryEntry(
        session_id="s1", agent_id="a1", key="k1", value="unique_search_term_alpha",
        tags=[], project="proj-alpha",
    )
    entry_b = MemoryEntry(
        session_id="s2", agent_id="a2", key="k2", value="unique_search_term_beta",
        tags=[], project="proj-beta",
    )
    await repo.store_memory(entry_a)
    await repo.store_memory(entry_b)

    results = await repo.search_memories(query="unique_search_term_alpha", session_id="s1", project="proj-alpha")
    assert len(results) >= 1
    assert results[0].key == "k1"

    results = await repo.search_memories(query="unique_search_term_beta", session_id="s2", project="proj-beta")
    assert len(results) >= 1
    assert results[0].key == "k2"
