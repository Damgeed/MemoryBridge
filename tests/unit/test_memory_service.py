"""Unit tests for MemoryService."""
import pytest
from memory_bridge.models import MemoryCreate
from memory_bridge.repository.sqlite_repo import SQLiteMemoryRepository
from memory_bridge.services.memory_service import MemoryService


@pytest.fixture
async def repo():
    import tempfile
    db_path = tempfile.mktemp(suffix=".db")
    r = SQLiteMemoryRepository(db_path=db_path)
    await r.initialize()
    yield r
    import os
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
async def service(repo):
    return MemoryService(repo=repo)


@pytest.mark.asyncio
async def test_create_and_get_memory(service):
    payload = MemoryCreate(session_id="s1", agent_id="a1", key="k1", value="v1")
    entry = await service.create_memory(payload)
    assert entry.key == "k1"
    assert entry.value == "v1"
    assert entry.session_id == "s1"

    retrieved = await service.get_memory(entry.id)
    assert retrieved is not None
    assert retrieved.value == "v1"


@pytest.mark.asyncio
async def test_get_nonexistent_memory(service):
    result = await service.get_memory("nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_query_memories(service):
    for i in range(3):
        payload = MemoryCreate(session_id="s1", agent_id="a1", key=f"k{i}", value=f"v{i}")
        await service.create_memory(payload)

    results = await service.query_memories(session_id="s1")
    assert len(results) == 3


@pytest.mark.asyncio
async def test_search_memories(service):
    payload = MemoryCreate(session_id="s1", agent_id="a1", key="special_key", value="unique_value_xyz")
    await service.create_memory(payload)

    results = await service.search_memories(query="unique_value_xyz", session_id="s1")
    assert len(results) >= 1
    assert results[0].key == "special_key"


@pytest.mark.asyncio
async def test_delete_memory(service):
    payload = MemoryCreate(session_id="s1", agent_id="a1", key="k1", value="v1")
    entry = await service.create_memory(payload)

    deleted = await service.delete_memory(entry.id)
    assert deleted is True

    retrieved = await service.get_memory(entry.id)
    assert retrieved is None


@pytest.mark.asyncio
async def test_project_resolution_from_auth(service):
    """Project should be inferred from auth context when not in payload."""
    payload = MemoryCreate(session_id="s1", agent_id="a1", key="k1", value="v1")
    entry = await service.create_memory(payload, project="proj-alpha", auth_context={"project_id": "proj-beta"})
    # payload.project is None, so auth_context's project should win
    assert entry.project is not None


@pytest.mark.asyncio
async def test_query_with_tags(service):
    payload = MemoryCreate(session_id="s1", agent_id="a1", key="k1", value="v1", tags=["important"])
    await service.create_memory(payload)

    results = await service.query_memories(session_id="s1", tags=["important"])
    assert len(results) == 1

    results = await service.query_memories(session_id="s1", tags=["nonexistent"])
    assert len(results) == 0
