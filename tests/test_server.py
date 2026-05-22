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
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/sessions/nonexistent")
        assert resp.status_code == 404


# --- Handoff Protocol Endpoints ---


@pytest.mark.asyncio
async def test_handoff_prepare_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a memory first
        await client.post("/memories", json={
            "session_id": "s-handoff", "agent_id": "agent_a",
            "key": "project", "value": "Memory Bridge",
        })

        # Prepare handoff
        resp = await client.post("/handoff/prepare", json={
            "from_agent_id": "agent_a",
            "to_agent_id": "agent_b",
            "session_id": "s-handoff",
            "context": {},
            "handoff_type": "summary",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "project" in data["context"]


@pytest.mark.asyncio
async def test_handoff_execute_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/memories", json={
            "session_id": "s-exec", "agent_id": "agent_a",
            "key": "user_name", "value": "Alice",
        })

        resp = await client.post("/handoff/execute", json={
            "from_agent_id": "agent_a",
            "to_agent_id": "agent_b",
            "session_id": "s-exec",
            "context": {},
            "handoff_type": "summary",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

        # Verify agent_b received the memory
        query_resp = await client.post("/memories/query", json={
            "agent_id": "agent_b",
        })
        assert query_resp.status_code == 200
        assert query_resp.json()["total"] >= 1


# --- Auth Middleware Tests ---

@pytest.fixture
def enable_auth():
    """Enable API key auth by setting MEMORY_BRIDGE_API_KEY."""
    old = os.environ.get("MEMORY_BRIDGE_API_KEY")
    os.environ["MEMORY_BRIDGE_API_KEY"] = "test-key-123"
    yield
    if old is None:
        del os.environ["MEMORY_BRIDGE_API_KEY"]
    else:
        os.environ["MEMORY_BRIDGE_API_KEY"] = old


@pytest.mark.asyncio
async def test_health_exempt_from_auth(enable_auth):
    """Health endpoint works without auth even when auth is enabled."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_required_when_key_set(enable_auth):
    """Endpoints return 401 when no auth header."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/memories", json={
            "session_id": "s1", "agent_id": "a1", "key": "k", "value": "v",
        })
        assert resp.status_code == 401
        assert "Bearer" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_auth_rejects_wrong_key(enable_auth):
    """Endpoints return 401 with wrong API key."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/memories",
            json={"session_id": "s1", "agent_id": "a1", "key": "k", "value": "v"},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_allows_valid_key(enable_auth):
    """Endpoints work with valid API key."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/memories",
            json={"session_id": "s1", "agent_id": "a1", "key": "k", "value": "v"},
            headers={"Authorization": "Bearer test-key-123"},
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_open_mode_no_key_needed():
    """Without MEMORY_BRIDGE_API_KEY, everything works without auth."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/memories", json={
            "session_id": "s1", "agent_id": "a1", "key": "k", "value": "v",
        })
        assert resp.status_code == 200


# --- TTL / Eviction API Tests ---


@pytest.mark.asyncio
async def test_create_memory_with_ttl():
    """Verify ttl_seconds flows through the API to the stored entry."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/memories", json={
            "session_id": "s-ttl-api",
            "agent_id": "a1",
            "key": "temp",
            "value": "will expire",
            "ttl_seconds": 300,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ttl_seconds"] == 300
        assert data["value"] == "will expire"


@pytest.mark.asyncio
async def test_create_memory_without_ttl_default():
    """Memory created without ttl_seconds defaults to None (never expires)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/memories", json={
            "session_id": "s-ttl-none",
            "agent_id": "a1",
            "key": "permanent",
            "value": "stays forever",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ttl_seconds"] is None
