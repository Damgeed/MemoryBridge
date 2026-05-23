"""Tenant resolution middleware.

Reads project info from the auth context and resolves it to a
PostgreSQL schema or RLS scope for multi-tenant data isolation.
"""

import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)

# In-memory schema cache: project_id -> tenant_schema
# In production, this would be loaded from the public.projects table
_schema_cache: dict[str, str] = {}


class TenantResolverMiddleware(BaseHTTPMiddleware):
    """Resolves project scope from auth context.

    Reads request.state.auth (set by APIKeyMiddleware) and sets:
    - request.state.project_id
    - request.state.tenant_schema
    """

    async def dispatch(self, request: Request, call_next):
        auth = getattr(request.state, "auth", None)
        if auth:
            project_id = auth.get("project_id")
            if project_id:
                request.state.project_id = project_id
                # Resolve schema from cache or database
                schema = _schema_cache.get(project_id)
                if schema:
                    request.state.tenant_schema = schema
                else:
                    # Default: use tenant_{project_id} pattern
                    request.state.tenant_schema = f"tenant_{project_id.replace('-', '_')}"
                    _schema_cache[project_id] = request.state.tenant_schema

        return await call_next(request)


async def get_tenant_schema(project_id: str) -> str:
    """Get the PostgreSQL schema name for a project.

    In Phase 3+, this looks up the projects table. For now,
    uses deterministic naming: tenant_{project_id}.
    """
    if project_id in _schema_cache:
        return _schema_cache[project_id]
    schema = f"tenant_{project_id.replace('-', '_')}"
    _schema_cache[project_id] = schema
    return schema
