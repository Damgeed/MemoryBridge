"""Unit tests for S3Store local fallback storage."""

import json
import os
import tempfile

import pytest
from memory_bridge.repository.s3_store import S3Store


@pytest.fixture
def local_dir():
    """Create a temporary directory for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def store(local_dir):
    """Create an S3Store with local-only fallback (S3 not enabled)."""
    s = S3Store()
    s._local_dir = local_dir
    # Ensure S3 is NOT enabled so we test the local path
    s._endpoint = ""
    s._access_key = ""
    return s


@pytest.mark.asyncio
async def test_store_and_retrieve_small_value(store, local_dir):
    """store() should write to disk and retrieve() should read it back."""
    memory_id = "test-001"
    value = {"hello": "world", "number": 42}

    s3_key = await store.store(memory_id, value)
    assert s3_key == f"memories/{memory_id}.json"

    # Verify the file exists on disk
    filepath = os.path.join(local_dir, f"{memory_id}.json")
    assert os.path.exists(filepath)

    # Read it back via retrieve
    retrieved = await store.retrieve(memory_id, s3_key)
    assert retrieved == value


@pytest.mark.asyncio
async def test_store_and_retrieve_large_value(store, local_dir):
    """needs_offloading should be True for values > 64KB."""
    large_value = "x" * (64 * 1024 + 1)
    assert store.needs_offloading(large_value) is True

    memory_id = "test-large-001"
    s3_key = await store.store(memory_id, large_value)
    assert s3_key is not None

    retrieved = await store.retrieve(memory_id, s3_key)
    assert retrieved == large_value


@pytest.mark.asyncio
async def test_retrieve_nonexistent(store):
    """retrieve() should return None for missing files."""
    result = await store.retrieve("nonexistent-id", "memories/nonexistent-id.json")
    assert result is None


@pytest.mark.asyncio
async def test_delete_removes_local_file(store, local_dir):
    """delete() should remove the local file."""
    memory_id = "test-del-001"
    value = {"data": "to-delete"}
    await store.store(memory_id, value)

    filepath = os.path.join(local_dir, f"{memory_id}.json")
    assert os.path.exists(filepath)

    result = await store.delete(memory_id, "memories/test-del-001.json")
    assert result is True
    assert not os.path.exists(filepath)


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_true(store):
    """delete() should return True even if file doesn't exist."""
    result = await store.delete("nonexistent", "memories/nonexistent.json")
    assert result is True


@pytest.mark.asyncio
async def test_needs_offloading_threshold(store):
    """needs_offloading should be False for small values, True for large."""
    small = "x" * 1000
    assert store.needs_offloading(small) is False

    # Just under threshold after JSON serialization (quotes add 2 bytes)
    under = "x" * (64 * 1024 - 3)  # serialized = 65535 bytes, < 65536
    assert store.needs_offloading(under) is False

    # Just over threshold
    over = "x" * (64 * 1024)  # serialized = 65538 bytes, > 65536
    assert store.needs_offloading(over) is True


@pytest.mark.asyncio
async def test_store_with_s3_enabled(store, local_dir):
    """When S3 is 'enabled' (env vars set), store should still write locally."""
    store._endpoint = "http://minio:9000"
    store._access_key = "test-key"
    assert store.enabled is True

    memory_id = "test-s3-001"
    value = {"hello": "from-s3-mode"}
    s3_key = await store.store(memory_id, value)
    assert s3_key is not None

    # File should still be on local disk
    filepath = os.path.join(local_dir, f"{memory_id}.json")
    assert os.path.exists(filepath)

    # retrieve should still work from local disk
    retrieved = await store.retrieve(memory_id, s3_key)
    assert retrieved == value


@pytest.mark.asyncio
async def test_serialize_complex_types(store, local_dir):
    """S3Store should handle complex JSON-serializable types."""
    value = {
        "list": [1, 2, 3],
        "nested": {"a": 1, "b": [True, False]},
        "null_val": None,
        "float": 3.14,
    }
    memory_id = "test-complex"
    s3_key = await store.store(memory_id, value)
    retrieved = await store.retrieve(memory_id, s3_key)
    assert retrieved == value
