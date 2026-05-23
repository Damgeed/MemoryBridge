"""Memory Bridge application factory.

Imports route handlers from dedicated controller modules and
assembles the FastAPI app with middleware and lifecycle management.
"""

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import APIKeyMiddleware
from .middleware.tenant import TenantResolverMiddleware
from .controllers import (
    admin_controller,
    auth_controller,
    handoff_controller,
    health_controller,
    memory_controller,
    session_controller,
)
from .dependencies import close_factory, get_storage
from .middleware.rate_limit import RedisRateLimiter
from .metrics import (
    request_counter,
    request_latency,
    uptime_gauge,
)
from .storage import MemoryStorage

logger = logging.getLogger(__name__)

# How often the background cleanup runs (seconds). Default: 5 minutes.
_CLEANUP_INTERVAL = int(os.environ.get("MEMORY_BRIDGE_CLEANUP_INTERVAL", "300"))
# Rate limit (requests per minute per IP). Default: 60.
_RATE_LIMIT = int(os.environ.get("MEMORY_BRIDGE_RATE_LIMIT", "60"))
# CORS origins (comma-separated). Default: allow all.
_CORS_ORIGINS = os.environ.get("MEMORY_BRIDGE_CORS_ORIGINS", "*").split(",")

_limiter = RedisRateLimiter(requests_per_minute=_RATE_LIMIT)


async def _cleanup_loop(storage: MemoryStorage):
    """Periodically delete expired memories."""
    while True:
        try:
            await asyncio.sleep(_CLEANUP_INTERVAL)
            # Warn if cleanup hasn't run in > 2x the interval
            last_cleanup = await storage.get_metric("last_cleanup_at")
            if last_cleanup is not None:
                last_cleanup_dt = datetime.fromisoformat(last_cleanup)
                seconds_since = (
                    datetime.now(timezone.utc) - last_cleanup_dt
                ).total_seconds()
                if seconds_since > 2 * _CLEANUP_INTERVAL:
                    logger.warning(
                        "Cleanup hasn't run in %.0f seconds (interval=%ds)",
                        seconds_since,
                        _CLEANUP_INTERVAL,
                    )
            deleted = await storage.cleanup_expired()
            if deleted:
                logger.info("Cleanup: deleted %d expired memories", deleted)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Cleanup task error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize storage and background cleanup on startup."""
    storage = await get_storage()
    await storage.initialize()

    # Initialize shared metrics (set-once, safe for multi-worker)
    await storage.initialize_metric("start_time", datetime.now(timezone.utc).isoformat())
    await storage.initialize_metric("request_count", 0)
    await storage.initialize_metric("total_latency_ms", 0.0)

    # Set up Prometheus uptime gauge (auto-updates on scrape)
    uptime_gauge.set_function(
        lambda: (datetime.now(timezone.utc) - _start_time).total_seconds()
    )

    # Start background cleanup task
    cleanup_task = asyncio.create_task(_cleanup_loop(storage))

    yield

    # Shutdown: cancel cleanup task
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    # Close connection pool if using PostgreSQL
    await close_factory()


def create_app() -> FastAPI:
    """Build and return a fully configured FastAPI application."""
    app = FastAPI(
        title="Memory Bridge",
        version="0.2.0",
        description="Cross-session memory persistence for multi-agent teams",
        lifespan=lifespan,
    )

    # ── Middleware Stack (order matters) ──────────────────────────────────────

    # 1. CORS — handle preflight and allow cross-origin requests
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. Auth — Bearer token check on non-/health routes
    app.add_middleware(APIKeyMiddleware)

    # 3. Tenant resolver — resolves project scope from auth context
    app.add_middleware(TenantResolverMiddleware)

    # --- Request Size Limit ---
    MAX_BODY_SIZE = int(
        os.environ.get("MEMORY_BRIDGE_MAX_BODY_SIZE", "10_485_760")
    )  # 10MB default

    class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
        """Rejects requests with body larger than MAX_BODY_SIZE."""

        async def dispatch(self, request, call_next):
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > MAX_BODY_SIZE:
                return Response(
                    status_code=413,
                    content=f'{{"detail":"Request body exceeds {MAX_BODY_SIZE} byte limit"}}',
                    media_type="application/json",
                )
            return await call_next(request)

    # 2.5. Request size limit — prevent oversized bodies
    app.add_middleware(RequestSizeLimitMiddleware)

    # 3. Core middleware — rate limiting, request ID, metrics recording
    @app.middleware("http")
    async def core_middleware(request: Request, call_next):
        """Consolidated middleware: rate limit → request ID → record metrics."""

        # Rate limit check (skip for health and metrics endpoints)
        if request.url.path not in ("/health", "/metrics"):
            client_ip = request.client.host if request.client else "unknown"
            allowed = await _limiter.check(client_ip)
            if not allowed:
                return Response(
                    content='{"detail":"Rate limit exceeded. Try again in 60 seconds."}',
                    status_code=429,
                    media_type="application/json",
                    headers={
                        "Retry-After": "60",
                        "X-Request-ID": "",
                    },
                )

        # Generate request ID
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # Process request
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - start) * 1000

        # Add request ID header
        response.headers["X-Request-ID"] = request_id

        # Record metrics (fire-and-forget, don't block the response)
        try:
            storage = await get_storage()
            await storage.increment_metric("request_count")
            await storage.increment_metric("total_latency_ms", round(elapsed_ms, 1))
        except Exception:
            logger.exception("Failed to record storage metrics")

        # Record Prometheus metrics
        request_counter.inc()
        request_latency.observe(elapsed_ms / 1000.0)

        return response

    # ── Routers ──────────────────────────────────────────────────────────────

    app.include_router(health_controller.router)
    app.include_router(auth_controller.router)
    app.include_router(memory_controller.router)
    app.include_router(session_controller.router)
    app.include_router(handoff_controller.router)
    app.include_router(admin_controller.router)

    return app


# Module-level start time for Prometheus uptime gauge
_start_time = datetime.now(timezone.utc)

# Module-level app instance for backward compatibility
# (from memory_bridge.main import app)
app = create_app()
