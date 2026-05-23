"""Unit tests for CacheService."""
import pytest
from memory_bridge.services.cache_service import CacheService


@pytest.mark.asyncio
async def test_cache_disabled_by_default():
    cache = CacheService()
    assert not cache.enabled


@pytest.mark.asyncio
async def test_get_returns_none_when_disabled():
    cache = CacheService()
    result = await cache.get_memory("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_set_does_nothing_when_disabled():
    cache = CacheService()
    # Should not raise even without Redis
    await cache.set_memory(None)
    assert True


@pytest.mark.asyncio
async def test_delete_does_nothing_when_disabled():
    cache = CacheService()
    await cache.delete_memory("any-id")
    assert True


@pytest.mark.asyncio
async def test_clear_does_nothing_when_disabled():
    cache = CacheService()
    await cache.clear()
    assert True


@pytest.mark.asyncio
async def test_enabled_with_mock_redis():
    """Test that CacheService initializes enabled with a Redis client."""
    cache = CacheService(redis_client=object(), default_ttl=60)
    assert cache.enabled
