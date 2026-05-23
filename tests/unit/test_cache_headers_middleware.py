"""Tests for CDN cache headers middleware."""

import pytest
from starlette.requests import Request
from starlette.responses import Response
from starlette.testclient import TestClient
from fastapi import FastAPI

from memory_bridge.middleware.cache_headers import CacheHeadersMiddleware


@pytest.fixture
def app():
    """Create a minimal FastAPI app with CacheHeadersMiddleware."""
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics():
        return {"requests": 42}

    @app.get("/memories")
    async def memories():
        return [{"id": 1, "content": "test"}]

    @app.post("/memories")
    async def create_memory():
        return {"id": 2}

    @app.put("/memories/1")
    async def update_memory():
        return {"id": 1}

    @app.delete("/memories/1")
    async def delete_memory():
        return {"ok": True}

    app.add_middleware(CacheHeadersMiddleware)
    return app


@pytest.fixture
def client(app):
    """Test client for the fixture app."""
    return TestClient(app)


class TestCacheHeadersMiddleware:
    """Tests for CacheHeadersMiddleware."""

    def test_health_endpoint_public_cache(self, client):
        """Health endpoint should get public, max-age=10."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.headers.get("Cache-Control") == "public, max-age=10"

    def test_metrics_endpoint_private_cache(self, client):
        """Metrics endpoint should get private, max-age=15."""
        response = client.get("/metrics")
        assert response.status_code == 200
        assert response.headers.get("Cache-Control") == "private, max-age=15"

    def test_generic_get_no_cache(self, client):
        """Other GET endpoints should get no-cache."""
        response = client.get("/memories")
        assert response.status_code == 200
        assert response.headers.get("Cache-Control") == "no-cache, no-store, must-revalidate"

    def test_post_no_store(self, client):
        """POST endpoints should get no-store."""
        response = client.post("/memories")
        assert response.status_code == 200
        assert response.headers.get("Cache-Control") == "no-store"

    def test_put_no_store(self, client):
        """PUT endpoints should get no-store."""
        response = client.put("/memories/1")
        assert response.status_code == 200
        assert response.headers.get("Cache-Control") == "no-store"

    def test_delete_no_store(self, client):
        """DELETE endpoints should get no-store."""
        response = client.delete("/memories/1")
        assert response.status_code == 200
        assert response.headers.get("Cache-Control") == "no-store"
