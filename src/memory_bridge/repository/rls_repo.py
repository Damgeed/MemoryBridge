"""PostgreSQL repository with Row-Level Security for shared-schema multi-tenancy.

Free/Starter tier projects share the `public` schema but use PostgreSQL RLS
policies to ensure each project can only access its own data.

Tenant identity is injected via ``SET app.current_project_id`` at the start
of each request (connection-level session variable).
"""

import logging
from typing import Optional

from .postgres_repo import PostgresMemoryRepository

logger = logging.getLogger(__name__)


class RLSMemoryRepository(PostgresMemoryRepository):
    """PostgreSQL backend with Row-Level Security.

    Instead of schema-per-tenant, Free/Starter tier projects share the
    ``public`` schema but use PostgreSQL RLS policies to ensure each
    project can only access its own data.

    Tenant identity is injected via ``SET app.current_project_id`` at the
    start of each request (connection-level session variable).
    """

    def __init__(self, pool, schema: str = "public"):
        super().__init__(pool, schema=schema)
        self._rls_enabled = False

    async def enable_rls(self) -> None:
        """Enable RLS on all tenant tables and create isolation policies."""
        async with self.pool.acquire() as conn:
            for table in ["memories", "sessions", "memory_tags"]:
                await conn.execute(
                    f"ALTER TABLE {self.schema}.{table} ENABLE ROW LEVEL SECURITY"
                )
                await conn.execute(
                    f"ALTER TABLE {self.schema}.{table} FORCE ROW LEVEL SECURITY"
                )

            # Drop existing policies first (idempotent — safe to re-run)
            for table in ["memories", "sessions"]:
                await conn.execute(
                    f"DROP POLICY IF EXISTS tenant_isolation ON {self.schema}.{table}"
                )
                await conn.execute(f"""
                    CREATE POLICY tenant_isolation ON {self.schema}.{table}
                    USING (
                        project = current_setting('app.current_project_id')::text
                    )
                """)

            # memory_tags does not have a project column; isolation is
            # inherited through the FK relationship to memories, so RLS on
            # memories cascades automatically.

        self._rls_enabled = True
        logger.info("RLS enabled on %s schema", self.schema)

    async def set_tenant_context(self, conn, project_id: str) -> None:
        """Set the tenant context for the current connection.

        Call this at the start of each request so that RLS policies filter
        rows to the current project.

        Uses ``$1`` parameterised form for safety (no SQL injection).
        """
        await conn.execute(
            "SELECT set_config($1, $2, true)",
            "app.current_project_id",
            project_id,
        )

    @property
    def rls_enabled(self) -> bool:
        """Whether RLS has been enabled on this repository instance."""
        return self._rls_enabled


async def create_repository(pool, project_id: str, tier: str = "free"):
    """Create the appropriate repository for a tenant based on tier.

    Parameters
    ----------
    pool : asyncpg.Pool
        The database connection pool.
    project_id : str
        The project identifier used for tenant isolation.
    tier : str, optional
        The pricing tier (``"free"``, ``"starter"``, ``"pro"``,
        ``"enterprise"``).  Free and Starter use RLS-based isolation;
        Pro and Enterprise use schema-per-tenant.

    Returns
    -------
    PostgresMemoryRepository
        Either a schema-per-tenant or RLS-based repository instance.
    """
    if tier in ("pro", "enterprise"):
        # Strong isolation: each project gets its own schema
        schema = f"tenant_{project_id.replace('-', '_')}"
        repo = PostgresMemoryRepository(pool=pool, schema=schema)
        await repo.initialize()
        logger.info(
            "Created schema-per-tenant repository (tier=%s, schema=%s)",
            tier,
            schema,
        )
        return repo

    # Free / Starter: shared schema with RLS
    repo = RLSMemoryRepository(pool=pool)
    await repo.initialize()
    await repo.enable_rls()
    logger.info(
        "Created RLS-based repository (tier=%s, project=%s)",
        tier,
        project_id,
    )
    return repo
