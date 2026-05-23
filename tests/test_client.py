"""Integration tests for the Memory Bridge Python client SDK.

Tests exercise the Client class against the real FastAPI application
using ASGITransport (no separate server process needed).
"""

from __future__ import annotations

import os
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from memory_bridge.dependencies import storage
from memory_bridge.main import app
from memory_bridge_client import Client, MemoryBridgeError


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def setup_storage():
    """Use a temp DB for each test (same pattern as test_server.py)."""
    os.environ["MEMORY_BRIDGE_ALLOW_OPEN"] = "true"
    db_path = tempfile.mktemp(suffix=".db")
    old_path = storage.db_path
    storage.db_path = db_path
    await storage.initialize()
    yield
    if os.path.exists(db_path):
        os.remove(db_path)
    storage.db_path = old_path


@pytest.fixture
def transport():
    return ASGITransport(app=app)


@pytest.fixture
async def client(transport):
    """Return a Client hooked to the test app via ASGITransport.

    We need to patch the underlying httpx.AsyncClient to use
    the ASGITransport so requests are served in-process.
    """
    c = Client(base_url="http://test")
    # Replace the real HTTP client with an ASGI one
    await c._client.aclose()
    c._client = AsyncClient(transport=transport, base_url="http://test")
    yield c
    await c._client.aclose()


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health(client: Client):
    data = await client.health()
    assert data["status"] == "ok"
    assert data["service"] == "memory-bridge"
    assert data["version"] == "0.2.0"
    assert isinstance(data["uptime_seconds"], int)
    assert isinstance(data["sessions_total"], int)
    assert isinstance(data["memories_total"], int)


# ------------------------------------------------------------------
# Memory CRUD
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_and_get_memory(client: Client):
    mem = await client.create_memory(
        session_id="s1",
        agent_id="a1",
        key="user_name",
        value="Alice",
        tags=["user-preference"],
    )
    assert mem["key"] == "user_name"
    assert mem["value"] == "Alice"
    assert mem["session_id"] == "s1"
    assert mem["agent_id"] == "a1"
    assert "id" in mem

    # Round-trip: get it back
    fetched = await client.get_memory(mem["id"])
    assert fetched["value"] == "Alice"
    assert fetched["id"] == mem["id"]


@pytest.mark.asyncio
async def test_query_memories(client: Client):
    await client.create_memory(
        session_id="sq1", agent_id="a1",
        key="k1", value="v1", tags=["important"],
    )
    await client.create_memory(
        session_id="sq1", agent_id="a1",
        key="k2", value="v2", tags=["normal"],
    )

    result = await client.query_memories(session_id="sq1", limit=10)
    assert result["total"] == 2
    assert len(result["entries"]) == 2

    # Filter by tag
    result = await client.query_memories(
        session_id="sq1", tags=["important"],
    )
    assert result["total"] == 1


@pytest.mark.asyncio
async def test_search_memories(client: Client):
    await client.create_memory(
        session_id="ss1", agent_id="a1",
        key="project", value="Memory Bridge is awesome",
    )
    await client.create_memory(
        session_id="ss1", agent_id="a1",
        key="user", value="Alice likes python programming",
    )

    result = await client.search_memories(q="python")
    assert result["total"] == 1
    assert result["entries"][0]["key"] == "user"

    # No matches
    result = await client.search_memories(q="zzzznotfound")
    assert result["total"] == 0
    assert result["entries"] == []


@pytest.mark.asyncio
async def test_delete_memory(client: Client):
    mem = await client.create_memory(
        session_id="s-del", agent_id="a1",
        key="temp", value="x",
    )
    mid = mem["id"]

    result = await client.delete_memory(mid)
    assert result["deleted"] is True

    # Confirm it's gone
    with pytest.raises(MemoryBridgeError) as exc:
        await client.get_memory(mid)
    assert exc.value.status_code == 404


# ------------------------------------------------------------------
# Session CRUD
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_and_get_session(client: Client):
    sess = await client.create_session(
        session_id="sess1",
        agent_id="agent_a",
        metadata={"env": "test"},
    )
    assert sess["session_id"] == "sess1"
    assert sess["agent_id"] == "agent_a"
    assert sess["metadata"] == {"env": "test"}

    fetched = await client.get_session("sess1")
    assert fetched["session_id"] == "sess1"
    assert fetched["agent_id"] == "agent_a"


@pytest.mark.asyncio
async def test_create_session_with_parent(client: Client):
    await client.create_session(
        session_id="parent", agent_id="a1",
    )
    child = await client.create_session(
        session_id="child", agent_id="a2",
        parent_session_id="parent",
    )
    assert child["parent_session_id"] == "parent"


# ------------------------------------------------------------------
# Handoff Protocol
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handoff_prepare(client: Client):
    # Seed a memory first
    await client.create_memory(
        session_id="s-handoff", agent_id="agent_a",
        key="project", value="Memory Bridge",
    )

    result = await client.handoff_prepare(
        from_agent_id="agent_a",
        to_agent_id="agent_b",
        session_id="s-handoff",
        context={},
        handoff_type="summary",
    )
    assert result["success"] is True
    assert "project" in result["context"]
    assert result["context"]["project"] == "Memory Bridge"


@pytest.mark.asyncio
async def test_handoff_execute(client: Client):
    await client.create_memory(
        session_id="s-exec", agent_id="agent_a",
        key="user_name", value="Alice",
    )

    result = await client.handoff_execute(
        from_agent_id="agent_a",
        to_agent_id="agent_b",
        session_id="s-exec",
        context={},
        handoff_type="summary",
    )
    assert result["success"] is True

    # Verify agent_b received the memory (handoff: prefix)
    q = await client.query_memories(agent_id="agent_b")
    assert q["total"] >= 1
    keys = [e["key"] for e in q["entries"]]
    assert any("handoff" in k for k in keys)


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_client_error_on_missing_memory(client: Client):
    """Getting a non-existent memory raises MemoryBridgeError with 404."""
    with pytest.raises(MemoryBridgeError) as exc:
        await client.get_memory("nonexistent-id")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_client_error_on_missing_session(client: Client):
    """Getting a non-existent session raises MemoryBridgeError with 404."""
    with pytest.raises(MemoryBridgeError) as exc:
        await client.get_session("nonexistent-session")
    assert exc.value.status_code == 404


# ------------------------------------------------------------------
# Auth header
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auth_header():
    """Client with api_key sends Bearer token."""
    c = Client(api_key="my-secret-key")
    assert c._client.headers["Authorization"] == "Bearer my-secret-key"
    await c.close()


@pytest.mark.asyncio
async def test_auth_integration():
    """When auth is enabled, client sends valid Bearer token."""
    os.environ["MEMORY_BRIDGE_API_KEY"] = "test-key-456"

    # Re-import app with auth enabled
    import importlib
    from memory_bridge import main as main_module
    importlib.reload(main_module)
    app_auth = main_module.app

    transport_auth = ASGITransport(app=app_auth)
    c = Client(base_url="http://test", api_key="test-key-456")
    await c._client.aclose()
    # Create transport client WITH the auth header
    c._client = AsyncClient(
        transport=transport_auth,
        base_url="http://test",
        headers={"Authorization": "Bearer test-key-456", "Content-Type": "application/json"},
    )

    try:
        # Health should still work (exempt from auth)
        health = await c.health()
        assert health["status"] == "ok"

        # Memory operations should work with valid key
        mem = await c.create_memory(
            session_id="s-auth", agent_id="a1",
            key="k", value="v",
        )
        assert mem["key"] == "k"
    finally:
        await c.close()
        del os.environ["MEMORY_BRIDGE_API_KEY"]
        # Re-reload to restore no-auth state for remaining tests
        importlib.reload(main_module)
