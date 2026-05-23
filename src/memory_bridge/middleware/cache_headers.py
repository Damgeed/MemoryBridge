"""CDN-friendly cache headers for HTTP responses.

Adds Cache-Control headers to appropriate endpoints
for CDN caching (Cloudflare, Fastly, etc.).
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class CacheHeadersMiddleware(BaseHTTPMiddleware):
    """Adds Cache-Control headers based on endpoint type.

    - Health endpoints: short cache (10s)
    - GET queries: short cache (30s) when no session filter
    - Mutating endpoints (POST, PUT, DELETE): no-cache
    - Static/docs: longer cache (1h)
    """

    CACHE_RULES = {
        "/health": {"max-age": 10, "public": True},
        "/metrics": {"max-age": 15, "public": False},
    }

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Only add cache headers to successful GET requests
        if request.method != "GET":
            response.headers["Cache-Control"] = "no-store"
            return response

        path = request.url.path

        # Apply specific cache rules
        if path in self.CACHE_RULES:
            rule = self.CACHE_RULES[path]
            if rule.get("public"):
                response.headers["Cache-Control"] = f"public, max-age={rule['max-age']}"
            else:
                response.headers["Cache-Control"] = f"private, max-age={rule['max-age']}"
            return response

        # Default for other GET endpoints: no cache (dynamic content)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response
