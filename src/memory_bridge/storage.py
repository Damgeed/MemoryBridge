"""Backward-compatible alias for SQLiteMemoryRepository.

This module re-exports MemoryStorage as a subclass of SQLiteMemoryRepository
so that existing imports like ``from .storage import MemoryStorage`` continue to work.
"""

from .repository.sqlite_repo import SQLiteMemoryRepository

# Re-export module-level helpers for any code that imports them from .storage
from .repository.sqlite_repo import (
    _hash_api_key,
    _row_to_entry,
    _is_expired,
    _filter_expired,
    _SCHEMA_MIGRATIONS,
)


class MemoryStorage(SQLiteMemoryRepository):
    """Backward-compatible alias for SQLiteMemoryRepository.

    All functionality is inherited from SQLiteMemoryRepository.
    """
    pass
