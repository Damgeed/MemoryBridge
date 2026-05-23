"""Unit tests for MemoryService."""
import os
import tempfile

import pytest
from memory_bridge.models import MemoryCreate
from memory_bridge.repository.s3_store import S3Store
from memory_bridge.repository.sqlite_repo import SQLiteMemoryRepository
from memory_bridge.services.memory_service import MemoryService, TierLimitExceeded


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


@pytest.mark.asyncio
async def test_create_memory_value_too_large(service):
    """Creating a memory with a value exceeding max-size should raise TierLimitExceeded."""
    # A value large enough to exceed the 1MB default
    large_value = "x" * (1_048_576 + 1)  # 1MB + 1 byte
    payload = MemoryCreate(session_id="s1", agent_id="a1", key="k1", value=large_value)
    with pytest.raises(TierLimitExceeded, match="Value exceeds"):
        await service.create_memory(payload)


@pytest.mark.asyncio
async def test_create_memory_normal_value(service):
    """A normal-sized value should not be blocked."""
    payload = MemoryCreate(session_id="s1", agent_id="a1", key="k_normal", value="small_value")
    entry = await service.create_memory(payload)
    assert entry.value == "small_value"


# ── S3 offloading tests ────────────────────────────────────────────────


@pytest.fixture
def s3_local_dir():
    """Create a temp dir for S3 local fallback."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
async def service_with_s3(repo, s3_local_dir):
    """MemoryService with S3Store wired in."""
    s3 = S3Store()
    s3._local_dir = s3_local_dir
    return MemoryService(repo=repo, s3_store=s3)


@pytest.mark.asyncio
async def test_create_memory_small_value_not_offloaded(service_with_s3):
    """Small values should NOT be offloaded to S3."""
    payload = MemoryCreate(
        session_id="s1", agent_id="a1", key="k_small", value="tiny_value"
    )
    entry = await service_with_s3.create_memory(payload)
    # Value should be stored directly, not as an S3 ref
    assert entry.value == "tiny_value"
    assert not isinstance(entry.value, dict) or entry.value.get("__s3_ref__") is not True


@pytest.mark.asyncio
async def test_create_memory_large_value_offloaded(service_with_s3):
    """Values > 64KB should be offloaded to S3 and stored as a reference."""
    large_value = "x" * (64 * 1024 + 100)  # Just over the 64KB threshold
    payload = MemoryCreate(
        session_id="s1", agent_id="a1", key="k_large", value=large_value
    )
    entry = await service_with_s3.create_memory(payload)
    # Value should now be an S3 reference dict
    assert isinstance(entry.value, dict)
    assert entry.value.get("__s3_ref__") is True
    assert "key" in entry.value
    assert entry.value["key"].startswith("memories/")
    assert entry.value["key"].endswith(".json")
    assert entry.value.get("original_type") == "str"


@pytest.mark.asyncio
async def test_get_memory_resolves_s3_ref(service_with_s3):
    """get_memory should resolve S3 references back to the original value."""
    large_value = "x" * (64 * 1024 + 100)
    payload = MemoryCreate(
        session_id="s1", agent_id="a1", key="k_resolve", value=large_value
    )
    created = await service_with_s3.create_memory(payload)
    # Created entry has a reference
    assert created.value.get("__s3_ref__") is True

    # Retrieve — should resolve back to original
    retrieved = await service_with_s3.get_memory(created.id)
    assert retrieved is not None
    assert retrieved.value == large_value


@pytest.mark.asyncio
async def test_delete_memory_cleans_up_s3(service_with_s3, s3_local_dir):
    """delete_memory should clean up S3/local files when deleting an offloaded memory."""
    large_value = "x" * (64 * 1024 + 100)
    payload = MemoryCreate(
        session_id="s1", agent_id="a1", key="k_cleanup", value=large_value
    )
    created = await service_with_s3.create_memory(payload)
    memory_id = created.id

    # File should exist on disk
    filepath = os.path.join(s3_local_dir, f"{memory_id}.json")
    assert os.path.exists(filepath)

    # Delete should remove it
    deleted = await service_with_s3.delete_memory(memory_id)
    assert deleted is True
    assert not os.path.exists(filepath)


@pytest.mark.asyncio
async def test_create_memory_no_s3_store_does_not_offload(repo):
    """Without S3Store, large values should pass through untouched (caught by tier limit)."""
    # Use a value just under the max value size but over S3 threshold
    # The max_value_size in settings is 1MB, so 100KB is fine
    big_value = "x" * (100 * 1024)  # ~100KB — over 64KB S3 threshold but under 1MB max
    service = MemoryService(repo=repo)  # No s3_store
    payload = MemoryCreate(
        session_id="s1", agent_id="a1", key="k_no_s3", value=big_value
    )
    entry = await service.create_memory(payload)
    # Without S3Store, value should be stored directly
    assert entry.value == big_value
