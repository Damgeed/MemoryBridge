"""API key authentication middleware for Memory Bridge."""

import os
from typing import Optional
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


def get_api_key() -> Optional[str]:
    """Read the API key from environment. Returns None if not configured."""
    return os.environ.get("MEMORY_BRIDGE_API_KEY", None)


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Middleware that checks Authorization: Bearer <key> on all routes.

    Exempts /health from auth checks.
    If MEMORY_BRIDGE_API_KEY is not set, auth is disabled (open mode).
    """

    EXEMPT_PATHS = {"/health", "/metrics", "/docs", "/openapi.json", "/redoc"}

    async def dispatch(self, request: Request, call_next):
        # Skip auth for exempt paths or when no key is configured
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        api_key = get_api_key()
        if api_key is None:
            # No key configured — open mode
            return await call_next(request)

        # Check Authorization header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header. Use: Bearer <key>"},
            )

        provided_key = auth_header.removeprefix("Bearer ").strip()
        if provided_key != api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid API key"},
            )

        return await call_next(request)
