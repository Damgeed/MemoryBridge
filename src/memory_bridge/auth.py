"""API key + JWT authentication middleware with dual-auth support."""

import logging
import os
from datetime import datetime, timezone

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import get_settings
from .dependencies import get_storage

logger = logging.getLogger(__name__)


EXEMPT_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc", "/playground", "/badge", "/graph"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validates Bearer token as either an API key or a JWT.

    Authentication methods (checked in order):
    1. MEMORY_BRIDGE_API_KEY env var (legacy single-key mode)
    2. API keys stored in the database
    3. JWT token (if MEMORY_BRIDGE_JWT_SECRET is configured)

    On success, request.state.auth is populated with key/identity info.
    Exempted paths skip authentication entirely.

    If neither env var, DB keys, nor JWT secret are configured, the middleware
    operates in open mode (no authentication required) only when
    MEMORY_BRIDGE_ALLOW_OPEN=true. Otherwise, all requests are rejected.
    """

    def __init__(self, app):
        super().__init__(app)
        self._has_checked_db_keys = False
        self._has_db_keys = False

    async def _check_db_has_keys(self) -> bool:
        """Check if any API keys exist in the database."""
        try:
            storage = await get_storage()
            keys = await storage.list_api_keys()
            return len(keys) > 0
        except Exception:
            return False

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in EXEMPT_PATHS or path.startswith("/playground/") or path.startswith("/graph/"):
            return await call_next(request)

        # Determine if auth is enforced
        env_key = os.environ.get("MEMORY_BRIDGE_API_KEY")

        if not self._has_checked_db_keys:
            self._has_db_keys = await self._check_db_has_keys()
            self._has_checked_db_keys = True

        if not env_key and not self._has_db_keys:
            open_mode = os.environ.get("MEMORY_BRIDGE_ALLOW_OPEN", "false").lower() == "true"
            if not open_mode:
                logger.warning(
                    "🚨 SECURITY: No API keys configured and MEMORY_BRIDGE_ALLOW_OPEN is not set. "
                    "All requests will be rejected. Set MEMORY_BRIDGE_API_KEY or create a key via /admin/keys."
                )
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required. No API keys configured on this server. "
                             "Set MEMORY_BRIDGE_API_KEY environment variable or create an API key."},
                )
            logger.warning("⚠️  OPEN MODE: MEMORY_BRIDGE_ALLOW_OPEN=true. No auth configured. Do NOT use in production.")
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header. Use: Bearer <token>"},
            )

        token = auth_header.removeprefix("Bearer ")

        # 1. Check against env var (legacy single-key mode)
        if env_key and token == env_key:
            request.state.auth = {"key_id": "env", "label": "env-key", "project_id": None}
            return await call_next(request)

        # 2. Check against stored API keys
        try:
            storage = await get_storage()
            key_info = await storage.authenticate_key(token)
            if key_info:
                request.state.auth = key_info
                return await call_next(request)
        except Exception:
            pass

        # 3. Try JWT authentication
        settings = get_settings()
        if settings.jwt_secret:
            try:
                payload = jwt.decode(
                    token,
                    settings.jwt_secret,
                    algorithms=[settings.jwt_algorithm],
                )
                if payload.get("sub"):
                    request.state.auth = {
                        "key_id": f"user:{payload['sub']}",
                        "label": f"user:{payload.get('username', '')}",
                        "project_id": payload.get("project_id"),
                        "user_id": payload["sub"],
                        "role": payload.get("role", "member"),
                    }
                    return await call_next(request)
            except jwt.ExpiredSignatureError:
                pass  # Fall through to 401
            except jwt.InvalidTokenError:
                pass  # Fall through to 401

        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid API key. Check your Authorization header."},
        )
