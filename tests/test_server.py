import pytest
import os
import tempfile
from httpx import AsyncClient, ASGITransport
from memory_bridge.main import app
from memory_bridge.dependencies import storage


@pytest.fixture(autouse=True)
async def setup_storage():
    """Use a temp DB for each test. Enable open mode for test suite."""
    os.environ["MEMORY_BRIDGE_ALLOW_OPEN"] = "true"
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
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "memory-bridge"
        assert data["version"] == "0.2.0"
        assert isinstance(data["uptime_seconds"], int)
        assert isinstance(data["sessions_total"], int)
        assert isinstance(data["memories_total"], int)
        assert isinstance(data["avg_latency_ms"], float)
        assert isinstance(data["requests_served"], int)


@pytest.mark.asyncio
async def test_health_returns_metrics():
    """Verify health endpoint returns all operational metrics fields."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        expected_keys = {
            "status", "service", "version", "uptime_seconds",
            "sessions_total", "memories_total", "avg_latency_ms", "requests_served",
            "last_cleanup_seconds_ago",
        }
        assert set(data.keys()) == expected_keys
        assert data["status"] == "ok"
        assert data["service"] == "memory-bridge"
        assert data["version"] == "0.2.0"
        assert data["uptime_seconds"] >= 0
        assert data["sessions_total"] >= 0
        assert data["memories_total"] >= 0
        assert data["avg_latency_ms"] >= 0.0
        assert data["requests_served"] >= 0


@pytest.mark.asyncio
async def test_health_returns_cleanup_monitoring():
    """Health endpoint includes last_cleanup_seconds_ago field."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "last_cleanup_seconds_ago" in data
        # When the test starts, cleanup hasn't run yet, so it should be None
        # But after any cleanup call it could be an int
        assert data["last_cleanup_seconds_ago"] is None or isinstance(data["last_cleanup_seconds_ago"], int)


# --- Prometheus Metrics Endpoint ---


@pytest.mark.asyncio
async def test_metrics_endpoint():
    """GET /metrics returns 200 with Prometheus content type and expected metrics."""
    os.environ["MEMORY_BRIDGE_PUBLIC_METRICS"] = "true"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")
        assert "charset=utf-8" in resp.headers.get("content-type", "")
        body = resp.text

        # Core metrics should be present
        assert "memory_bridge_http_requests_total" in body
        assert "memory_bridge_memories" in body
        assert "memory_bridge_sessions" in body
        assert "memory_bridge_uptime_seconds" in body
        assert "memory_bridge_request_latency_seconds" in body


@pytest.mark.asyncio
async def test_metrics_updates_gauges():
    """After storing memories and sessions, the gauges reflect current counts."""
    os.environ["MEMORY_BRIDGE_PUBLIC_METRICS"] = "true"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a session and a memory
        await client.post("/sessions", json={"session_id": "s-metric", "agent_id": "a1"})
        await client.post("/memories", json={
            "session_id": "s-metric", "agent_id": "a1",
            "key": "k1", "value": "v1",
        })

        resp = await client.get("/metrics")
        body = resp.text
        # Gauges should show at least 1
        assert "memory_bridge_memories 1" in body or "memory_bridge_memories 1." in body
        assert "memory_bridge_sessions 1" in body or "memory_bridge_sessions 1." in body


@pytest.mark.asyncio
async def test_metrics_exempt_from_auth(enable_auth):
    """Metrics endpoint requires auth by default when auth is enabled."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/metrics")
        assert resp.status_code == 401


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
async def test_query_memories_with_offset():
    """Query endpoint respects the offset parameter."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Store 5 memories
        for i in range(5):
            await client.post("/memories", json={
                "session_id": "s-offset", "agent_id": "a1",
                "key": f"k{i}", "value": f"v{i}",
            })

        # Query with limit=5, offset=0 -> get all 5
        resp = await client.post("/memories/query", json={
            "session_id": "s-offset",
            "limit": 5,
            "offset": 0,
        })
        assert resp.status_code == 200
        assert resp.json()["total"] == 5

        # Query with offset=2 -> skip 2, expect 3
        resp = await client.post("/memories/query", json={
            "session_id": "s-offset",
            "limit": 5,
            "offset": 2,
        })
        assert resp.status_code == 200
        assert resp.json()["total"] == 3

        # Query with offset=10 (beyond available) -> expect 0
        resp = await client.post("/memories/query", json={
            "session_id": "s-offset",
            "limit": 5,
            "offset": 10,
        })
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


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
async def test_search_memories_endpoint():
    """Hit /memories/search?q=... and verify response."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Store some memories
        await client.post("/memories", json={
            "session_id": "s1", "agent_id": "a1",
            "key": "project", "value": "Memory Bridge is awesome",
        })
        await client.post("/memories", json={
            "session_id": "s1", "agent_id": "a1",
            "key": "user", "value": "Alice likes python programming",
        })
        await client.post("/memories", json={
            "session_id": "s2", "agent_id": "a2",
            "key": "note", "value": "completely unrelated content",
        })

        # Search for "python"
        resp = await client.get("/memories/search", params={"q": "python"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["entries"][0]["key"] == "user"

        # Search with session filter
        resp = await client.get("/memories/search", params={
            "q": "Memory", "session_id": "s1",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["entries"][0]["key"] == "project"

        # Search with no matches
        resp = await client.get("/memories/search", params={"q": "zzzznotfound"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["entries"] == []


@pytest.mark.asyncio
async def test_search_memories_endpoint_with_filters():
    """Search endpoint respects agent_id and session_id filters."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/memories", json={
            "session_id": "s-alice", "agent_id": "alice",
            "key": "k1", "value": "alice secret data",
        })
        await client.post("/memories", json={
            "session_id": "s-bob", "agent_id": "bob",
            "key": "k2", "value": "bob secret data",
        })

        # Filter by agent
        resp = await client.get("/memories/search", params={
            "q": "secret", "agent_id": "alice",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["entries"][0]["agent_id"] == "alice"

        # Filter by session
        resp = await client.get("/memories/search", params={
            "q": "secret", "session_id": "s-bob",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["entries"][0]["session_id"] == "s-bob"


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
    """Without MEMORY_BRIDGE_ALLOW_OPEN=true, open mode is disabled and requests fail with 401."""
    # Temporarily remove ALLOW_OPEN to verify the default behavior
    old = os.environ.pop("MEMORY_BRIDGE_ALLOW_OPEN", None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/memories", json={
            "session_id": "s1", "agent_id": "a1", "key": "k", "value": "v",
        })
        assert resp.status_code == 401
    if old:
        os.environ["MEMORY_BRIDGE_ALLOW_OPEN"] = old


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


# --- Production Hardening Tests (v0.4) ---


@pytest.mark.asyncio
async def test_cors_headers():
    """Response includes Access-Control-Allow-Origin header when request has Origin."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health", headers={"Origin": "http://example.com"})
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers
        # Value varies by Starlette version — either "*" or echo'd origin


@pytest.mark.asyncio
async def test_request_id_header():
    """Response includes X-Request-ID header."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert "x-request-id" in resp.headers
        rid = resp.headers["x-request-id"]
        assert len(rid) == 36
        assert rid.count("-") == 4


@pytest.mark.asyncio
async def test_rate_limit():
    """When rate limit is low, requests beyond the limit get 429."""
    os.environ["MEMORY_BRIDGE_RATE_LIMIT"] = "5"
    import importlib
    from memory_bridge import main as main_module
    importlib.reload(main_module)
    app2 = main_module.app

    transport = ASGITransport(app=app2)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for i in range(5):
            resp = await client.post("/memories", json={
                "session_id": "s-rate", "agent_id": "a1",
                "key": f"k{i}", "value": f"v{i}",
            })
            assert resp.status_code == 200, f"Request {i} should succeed"

        resp = await client.post("/memories", json={
            "session_id": "s-rate", "agent_id": "a1",
            "key": "k6", "value": "v6",
        })
        assert resp.status_code == 429
        data = resp.json()
        assert "rate limit" in data["detail"].lower()
        assert "retry-after" in resp.headers or "Retry-After" in resp.headers

    del os.environ["MEMORY_BRIDGE_RATE_LIMIT"]
    importlib.reload(main_module)


# --- Agent Lineage API Tests ---


@pytest.mark.asyncio
async def test_query_with_lineage():
    """Create memories under parent, query child session with include_lineage=True."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create parent session
        await client.post("/sessions", json={
            "session_id": "s-parent-api",
            "agent_id": "a1",
        })
        # Create child session with parent reference
        await client.post("/sessions", json={
            "session_id": "s-child-api",
            "agent_id": "a2",
            "parent_session_id": "s-parent-api",
        })

        # Store a memory under parent
        await client.post("/memories", json={
            "session_id": "s-parent-api",
            "agent_id": "a1",
            "key": "shared_info",
            "value": "from parent",
            "tags": ["shared"],
        })

        # Query child without lineage — should be empty
        resp = await client.post("/memories/query", json={
            "session_id": "s-child-api",
        })
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        # Query child WITH lineage — should see parent's memory
        resp = await client.post("/memories/query?include_lineage=true", json={
            "session_id": "s-child-api",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["entries"][0]["key"] == "shared_info"
        assert data["entries"][0]["value"] == "from parent"

        # Store a child memory with the same key — child should win via lineage
        await client.post("/memories", json={
            "session_id": "s-child-api",
            "agent_id": "a2",
            "key": "shared_info",
            "value": "from child",
        })

        resp = await client.post("/memories/query?include_lineage=true", json={
            "session_id": "s-child-api",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["entries"][0]["value"] == "from child"


# --- Admin API Key Management Endpoints ---


@pytest.mark.asyncio
async def test_create_api_key_endpoint():
    """POST /admin/keys creates a new API key."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/keys?label=test-key")
        assert resp.status_code == 200
        data = resp.json()
        assert "key" in data
        assert data["key"].startswith("mb_")
        assert data["label"] == "test-key"
        assert data["is_active"] is True


@pytest.mark.asyncio
async def test_list_api_keys_endpoint():
    """GET /admin/keys returns all keys."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/keys")
        assert resp.status_code == 200
        data = resp.json()
        assert "keys" in data


@pytest.mark.asyncio
async def test_revoke_api_key_endpoint():
    """DELETE /admin/keys/:id revokes a key."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a key
        create_resp = await client.post("/admin/keys?label=revoke-me")
        assert create_resp.status_code == 200
        key_id = create_resp.json()["id"]

        # Revoke it
        resp = await client.delete(f"/admin/keys/{key_id}")
        assert resp.status_code == 200
        assert resp.json()["revoked"] is True

        # Verify it's revoked
        list_resp = await client.get("/admin/keys")
        keys = list_resp.json()["keys"]
        revoked_key = next(k for k in keys if k["id"] == key_id)
        assert revoked_key["is_active"] == 0 or revoked_key["is_active"] is False


@pytest.mark.asyncio
async def test_revoke_nonexistent_key_404():
    """DELETE /admin/keys/:id on nonexistent returns 404."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete("/admin/keys/nonexistent-id")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_key_with_project():
    """POST /admin/keys supports project scoping."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/keys?label=project-key&project_id=proj-alpha")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "proj-alpha"
