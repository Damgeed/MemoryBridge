"""Centralized configuration for Memory Bridge.

All settings are read from environment variables with MEMORY_BRIDGE_ prefix.
Usage::

    from memory_bridge.config import get_settings
    settings = get_settings()
"""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database — pick one backend
    use_sqlite: bool = True
    database_url: str = "memory_bridge.db"  # SQLite path or PostgreSQL DSN

    # PostgreSQL connection pool settings
    pool_min_size: int = 5
    pool_max_size: int = 20
    pool_max_queries: int = 50000
    pool_max_inactive_connection_lifetime: float = 300.0
    command_timeout: int = 30

    # Auth
    api_key: str = ""
    allow_open: bool = False
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # Rate limiting
    rate_limit_per_minute: int = 60
    rate_limit_backend: str = "memory"  # "memory" | "redis"

    # Rate limiting by tier (requests per minute)
    rate_limit_free: int = 60
    rate_limit_starter: int = 300
    rate_limit_pro: int = 600
    rate_limit_enterprise: int = 6000

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Security
    max_body_size: int = 10_485_760  # 10MB
    public_metrics: bool = False

    # Value-size enforcement
    max_value_size: int = 1_048_576  # 1MB default for individual value

    # Memory lifecycle
    cleanup_interval: int = 300
    default_ttl: int = 0  # 0 = no default TTL

    # Server
    port: int = 8000
    reload: bool = False

    # CORS
    cors_origins: str = "http://localhost:8000,https://*.railway.app"

    model_config = {"env_prefix": "MEMORY_BRIDGE_"}


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings singleton."""
    return Settings()
