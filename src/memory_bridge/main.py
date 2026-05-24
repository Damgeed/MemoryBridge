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
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import APIKeyMiddleware
from .middleware.tenant import TenantResolverMiddleware
from .controllers import (
    admin_controller,
    auth_controller,
    badge_controller,
    billing_controller,
    export_controller,
    graph_controller,
    handoff_controller,
    health_controller,
    memory_controller,
    playground_controller,
    pricing_controller,
    session_controller,
)
from .webhooks import router as webhook_router
from .webhooks.webhook_controller import get_webhook_service
from .dependencies import close_factory, get_storage
from .events.event_bus import EventBus
from .middleware.rate_limit import RedisRateLimiter
from .metrics import (
    request_counter,
    request_latency,
    uptime_gauge,
)
from .services.audit_service import AuditService
from .storage import MemoryStorage

logger = logging.getLogger(__name__)

# How often the background cleanup runs (seconds). Default: 5 minutes.
_CLEANUP_INTERVAL = int(os.environ.get("MEMORY_BRIDGE_CLEANUP_INTERVAL", "300"))
# Rate limit (requests per minute per IP). Default: 60.
_RATE_LIMIT = int(os.environ.get("MEMORY_BRIDGE_RATE_LIMIT", "60"))
# CORS origins (comma-separated). Default: allow all.
_CORS_ORIGINS = os.environ.get("MEMORY_BRIDGE_CORS_ORIGINS", "https://memorybridge.io,http://localhost:8000").split(",")

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

    # Warn if JWT secret is not configured but auth is enabled
    from .config import get_settings
    _settings = get_settings()
    if not _settings.jwt_secret:
        env_key = os.environ.get("MEMORY_BRIDGE_API_KEY")
        if env_key or not _settings.allow_open:
            logger.warning(
                "⚠️  JWT_SECRET is not configured (MEMORY_BRIDGE_JWT_SECRET not set). "
                "JWT-based authentication and token refresh will fail with a RuntimeError. "
                "For API-key-only operation this is fine; set MEMORY_BRIDGE_JWT_SECRET if you "
                "need JWT authentication."
            )

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

    # Load persisted webhook subscriptions from the repository
    webhook_svc = await get_webhook_service(repo=storage)
    await webhook_svc.load_subscriptions()

    # Initialize audit service and subscribe to EventBus events
    audit_svc = AuditService(repo=storage)

    # Try to get EventBus from webhook service if available
    event_bus = getattr(webhook_svc, '_event_bus', None)
    if event_bus is None:
        event_bus = EventBus()

    # Subscribe audit service to all event types
    async def audit_event_handler(data: dict) -> None:
        event_type = data.get("type", "unknown")
        event_data = data.get("data", {}) if isinstance(data, dict) else data
        await audit_svc.record(
            action=event_type,
            actor_type="system",
            actor_id="event_bus",
            resource_type=event_data.get("resource_type", ""),
            resource_id=event_data.get("resource_id"),
            project_id=event_data.get("project"),
            details=event_data,
        )

    for et in [
        "memory.created",
        "memory.updated",
        "memory.deleted",
        "memory.searched",
        "session.created",
        "handoff.executed",
    ]:
        event_bus.subscribe(et, audit_event_handler)

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
            key_id = None
            tier = "free"
            if hasattr(request.state, "auth") and request.state.auth:
                # Support both "id" (DB keys) and "key_id" (env/JWT) field names
                key_id = request.state.auth.get("id") or request.state.auth.get("key_id")
                tier = request.state.auth.get("tier", "free")
            allowed = await _limiter.check_with_key(
                key_id=key_id, tier=tier, client_ip=client_ip
            )
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
            await storage.increment_metric("total_latency_ms", int(round(elapsed_ms, 1)))
        except Exception:
            logger.exception("Failed to record storage metrics")

        # Record Prometheus metrics
        request_counter.inc()
        request_latency.observe(elapsed_ms / 1000.0)

        return response

    # ── Routers ──────────────────────────────────────────────────────────────

    app.include_router(health_controller.router)
    app.include_router(auth_controller.router)
    app.include_router(badge_controller.router)
    app.include_router(billing_controller.router)
    app.include_router(memory_controller.router)
    app.include_router(playground_controller.router)
    app.include_router(pricing_controller.router)
    app.include_router(session_controller.router)
    app.include_router(graph_controller.router)
    app.include_router(handoff_controller.router)
    app.include_router(admin_controller.router)
    app.include_router(export_controller.router)
    app.include_router(webhook_router)

    # ── Root Landing Page ──────────────────────────────────
    @app.get("/", include_in_schema=False)
    async def root():
        """Serve the landing page."""
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        html_path = os.path.join(static_dir, "index.html")
        if os.path.exists(html_path):
            with open(html_path) as f:
                content = f.read()
            return Response(
                content=content,
                media_type="text/html",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
        return Response(
            content='{"service":"memory-bridge","docs":"/docs","playground":"/playground/"}',
            media_type="application/json",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    # ── Static Assets (logo.svg, etc.) ─────────────────────────────
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.exists(static_dir):
        app.mount("/playground", StaticFiles(directory=static_dir, html=False), name="playground-assets")

    return app


# Module-level start time for Prometheus uptime gauge
_start_time = datetime.now(timezone.utc)

# Module-level app instance for backward compatibility
# (from memory_bridge.main import app)
app = create_app()
