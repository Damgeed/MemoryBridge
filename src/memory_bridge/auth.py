"""API key + JWT authentication middleware with dual-auth support."""

import hashlib
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

# ---------------------------------------------------------------------------
# JWT blacklist — in-memory set of revoked token identifiers.
# On server restart the blacklist is cleared; tokens with short TTL (< 24h)
# are effectively bounded, so this is acceptable for production use.
# ---------------------------------------------------------------------------
_token_blacklist: set[str] = set()


def _token_id(claims: dict) -> str:
    """Produce a unique, stable identifier for a JWT's claims dict.

    Uses ``sub`` + ``iat`` (preferred), falling back to ``sub`` + ``exp``,
    then finally ``sub`` alone.  The value is a hex-encoded SHA-256 hash.
    """
    sub = claims.get("sub", "")
    ts = claims.get("iat") or claims.get("exp") or ""
    return hashlib.sha256(f"{sub}:{ts}".encode()).hexdigest()


def revoke_token(token: str) -> None:
    """Decode *token* and add its identifier to the in-memory blacklist.

    The token must be valid (not expired, not tampered) so we can extract
    ``sub`` / ``iat`` claims.  Silently ignores decode failures
    (e.g. already-expired tokens).
    """
    settings = get_settings()
    if not settings.jwt_secret:
        logger.warning("revoke_token called but no JWT secret is configured — ignoring")
        return
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False},  # allow revoking even near-expiry tokens
        )
        tid = _token_id(payload)
        _token_blacklist.add(tid)
        logger.info("Token %s… revoked (id=%s)", token[:12], tid[:12])
    except jwt.InvalidTokenError:
        logger.warning("revoke_token received an invalid token — ignored")


def is_token_revoked(claims: dict) -> bool:
    """Return ``True`` if the token identified by *claims* has been revoked."""
    return _token_id(claims) in _token_blacklist

# Well-known demo key for public playground testing (5 req/min rate limit)
DEMO_API_KEY = "mb_demo_public_test"


EXEMPT_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc", "/playground", "/badge", "/graph", "/graph/data", "/billing/webhook", "/pricing", "/concept", "/concept/", "/dashboard", "/dashboard/", "/dashboard/recover", "/dashboard/free-signup", "/dashboard/stripe-welcome", "/auth/register", "/auth/login", "/auth/oauth", "/auth/auth0/login", "/auth/auth0/callback", "/auth/auth0/passwordless/start", "/auth/auth0/passwordless/start-sms", "/auth/auth0/passwordless/verify", "/demo", "/forgetful-demo", "/watch-demo", "/integrations", "/integration/langchain", "/integration/crewai", "/integration/autogen", "/integration/openai-agents", "/api-docs", "/faq", "/CAPABILITIES.md"}


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
        if path in EXEMPT_PATHS or path.startswith("/playground/"):
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
            logger.warning("  OPEN MODE: MEMORY_BRIDGE_ALLOW_OPEN=true. No auth configured. Do NOT use in production.")
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

        # 2. Check against well-known demo key (rate-limited to 5 req/min)
        if token == DEMO_API_KEY:
            request.state.auth = {"key_id": "demo:public", "label": "demo-key", "tier": "demo", "project_id": None}
            return await call_next(request)

        # 3. Check against stored API keys
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
                    # --- blacklist check ---
                    if is_token_revoked(payload):
                        return JSONResponse(
                            status_code=401,
                            content={"detail": "Token has been revoked"},
                        )
                    request.state.auth = {
                        "key_id": f"user:{payload['sub']}",
                        "label": f"user:{payload.get('username', '')}",
                        "project_id": payload.get("project_id") or payload.get("organization_id"),
                        "user_id": payload["sub"],
                        "role": payload.get("role", "member"),
                        "user_email": payload.get("email", ""),
                        "user_name": payload.get("name", ""),
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
