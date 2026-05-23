"""Dependency injection container for Memory Bridge.

Provides factory-pattern access to repositories with proper
lifecycle management for connection pools."""

import logging
from typing import Optional

import asyncpg

from memory_bridge.config import get_settings
from memory_bridge.repository import MemoryRepository
from memory_bridge.repository.sqlite_repo import SQLiteMemoryRepository
from memory_bridge.repository.postgres_repo import PostgresMemoryRepository

logger = logging.getLogger(__name__)

# Keep old singleton for backward compat in tests
# Tests directly set storage.db_path and call storage.initialize()
storage = SQLiteMemoryRepository(db_path="memory_bridge.db")


class RepositoryFactory:
    """Manages repository lifecycle and connection pooling."""

    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None
        self._settings = get_settings()

    async def get_repository(self, schema: str = "public") -> MemoryRepository:
        """Get a repository instance for the given schema."""
        settings = self._settings
        if settings.use_sqlite:
            # Return the module-level singleton for backward compatibility
            # Tests rely on setting storage.db_path directly on this instance
            return storage
        else:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(
                    dsn=settings.database_url,
                    min_size=settings.pool_min_size,
                    max_size=settings.pool_max_size,
                    max_queries=settings.pool_max_queries,
                    max_inactive_connection_lifetime=settings.pool_max_inactive_connection_lifetime,
                    command_timeout=settings.command_timeout,
                )
            repo = PostgresMemoryRepository(pool=self._pool, schema=schema)
            await repo.initialize()
            return repo

    async def close(self):
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
        logger.info("RepositoryFactory closed")


# Factory singleton with lifecycle management
_factory = RepositoryFactory()


async def get_storage(schema: str = "public") -> MemoryRepository:
    """Dependency injection for FastAPI endpoints."""
    return await _factory.get_repository(schema=schema)


async def close_factory():
    """Shutdown hook for graceful pool closure."""
    await _factory.close()
