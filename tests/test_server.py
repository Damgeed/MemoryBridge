import pytest
import os
import tempfile
from httpx import AsyncClient, ASGITransport
from memory_bridge.main import app
from memory_bridge.dependencies import storage


@pytest.fixture(autouse=True)
async def setup_storage():
    """Use a temp DB for each test."""
    db_path = tempfile.mktemp(suffix=".db")
    old_path = storage.db_path
    storage.db_path = db_path
    await storage.initialize()
    yield
    if os.path.exists(db_path):
        os.remove(db_path)
    storage.db_path = old_path


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "service": "memory-bridge"}


@pytest.mark.asyncio
async def test_create_and_get_memory():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/memories", json={
            "session_id": "s1",
            "agent_id": "a1",
            "key": "user_name",
            "value": "Alice",
            "tags": ["user-preference"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["key"] == "user_name"
        mem_id = data["id"]

        resp = await client.get(f"/memories/{mem_id}")
        assert resp.status_code == 200
        assert resp.json()["value"] == "Alice"


@pytest.mark.asyncio
async def test_query_memories():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/memories", json={
            "session_id": "s1", "agent_id": "a1",
            "key": "k1", "value": "v1", "tags": ["important"]
        })
        await client.post("/memories", json={
            "session_id": "s1", "agent_id": "a1",
            "key": "k2", "value": "v2", "tags": ["normal"]
        })

        resp = await client.post("/memories/query", json={
            "session_id": "s1",
            "limit": 10,
        })
        assert resp.status_code == 200
        assert resp.json()["total"] == 2


@pytest.mark.asyncio
async def test_delete_memory():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/memories", json={
            "session_id": "s1", "agent_id": "a1",
            "key": "temp", "value": "x"
        })
        mem_id = resp.json()["id"]

        resp = await client.delete(f"/memories/{mem_id}")
        assert resp.status_code == 200

        resp = await client.get(f"/memories/{mem_id}")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_404_on_missing_memory():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/memories/nonexistent")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_and_get_session():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/sessions", json={
            "session_id": "s1",
            "agent_id": "a1",
        })
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "s1"

        resp = await client.get("/sessions/s1")
        assert resp.status_code == 200
        assert resp.json()["agent_id"] == "a1"


@pytest.mark.asyncio
async def test_404_on_missing_session():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http/test") as client:
        resp = await client.get("/sessions/nonexistent")
        assert resp.status_code == 404
