"""Migration runner for dual-backend (SQLite / PostgreSQL) support.

Reads SQL migration files from backend-specific subdirectories,
applies them in version order, and tracks applied versions in a
``schema_version`` table.

Supports blue-green (zero-downtime) deployments via a migration
guard that warns on backward-incompatible operations.
"""

import logging
import re
from pathlib import Path
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)


class MigrationError(RuntimeError):
    """Raised when a migration step fails."""


# Regex to extract the numeric version from filenames like ``001_initial.sql``
_VERSION_RE = re.compile(r"^(\d+)_.*\.sql$")


def _parse_version(filename: str) -> Optional[int]:
    """Extract the migration version number from a SQL filename."""
    m = _VERSION_RE.match(filename)
    if m is not None:
        return int(m.group(1))
    return None


# ------------------------------------------------------------------
# Blue-green deployment guard
# ------------------------------------------------------------------

# Patterns for SQL operations that break backward compatibility.
# Any migration containing these will trigger a warning at deploy time.
_DESTRUCTIVE_PATTERNS: list[tuple[str, str]] = [
    (r'DROP\s+COLUMN', 'DROP COLUMN breaks backward compatibility'),
    (r'DROP\s+TABLE', 'DROP TABLE breaks backward compatibility'),
    (r'DROP\s+SCHEMA', 'DROP SCHEMA breaks backward compatibility'),
    (r'ALTER\s+COLUMN.*DROP\s+DEFAULT', 'Dropping default may break old readers'),
    (r'ALTER\s+COLUMN.*SET\s+NOT\s+NULL', 'Adding NOT NULL to existing column breaks old readers'),
    (r'RENAME\s+COLUMN', 'RENAME COLUMN breaks backward compatibility'),
    (r'RENAME\s+TABLE', 'RENAME TABLE breaks backward compatibility'),
]


def check_backward_compatible(sql_file_path: Union[str, Path]) -> list[str]:
    """Check if a migration SQL file contains backward-incompatible changes.

    Scans the file for destructive SQL operations (DROP, RENAME,
    ALTER ... NOT NULL, etc.) that would break an older application
    version still running against the same schema.

    Parameters
    ----------
    sql_file_path : str or Path
        Path to the ``.sql`` migration file to inspect.

    Returns
    -------
    list of str
        Warning messages.  An empty list means the migration is safe.
    """
    with open(sql_file_path, encoding="utf-8") as f:
        content = f.read().upper()

    warnings: list[str] = []
    for pattern, message in _DESTRUCTIVE_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            warnings.append(f"  {message}")
    return warnings


class MigrationRunner:
    """Runs SQL migration files against a database connection.

    Parameters
    ----------
    migrations_dir : str or Path
        Root directory that contains ``sqlite/`` and ``postgresql/``
        subdirectories with ``NNN_name.sql`` migration files.
    backend : str
        Either ``"sqlite"`` or ``"postgresql"``.
    """

    def __init__(
        self,
        migrations_dir: Union[str, Path],
        backend: str = "sqlite",
    ):
        self.migrations_dir = Path(migrations_dir)
        if backend not in ("sqlite", "postgresql"):
            raise ValueError(f"Unsupported backend: {backend!r}")
        self.backend = backend
        self._backend_dir = self.migrations_dir / backend

        if not self._backend_dir.is_dir():
            raise FileNotFoundError(
                f"Migration directory not found: {self._backend_dir}"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, conn_or_db: Any) -> list[str]:
        """Apply all pending migrations.

        Parameters
        ----------
        conn_or_db :
            An open database connection.  For SQLite this should be an
            ``aiosqlite.Connection``; for PostgreSQL an ``asyncpg.Connection``
            (or equivalent object with ``execute`` / ``executescript``).

        Returns
        -------
        list of str
            The names (e.g. ``"001_initial.sql"``) of every migration that was
            applied during this call.
        """
        # 1. Ensure the schema_version tracking table exists
        await self._ensure_schema_version(conn_or_db)

        # 2. Discover migration files, sorted by version
        migrations = self._discover_migrations()

        # 3. Find already-applied versions
        applied = await self._get_applied_versions(conn_or_db)

        # 4. Check backward compatibility and apply each pending migration
        applied_names: list[str] = []
        for version, filepath in migrations:
            if version in applied:
                continue
            name = filepath.name
            logger.info("Applying migration %s (%s)", name, self.backend)

            # Blue-green guard: warn on backward-incompatible operations
            compat_warnings = check_backward_compatible(filepath)
            for warning in compat_warnings:
                logger.warning("Migration %s: %s", name, warning)

            try:
                # Acquire advisory lock before applying migration
                if self.backend == "sqlite":
                    await conn_or_db.execute("BEGIN IMMEDIATE")
                elif self.backend == "postgresql":
                    await conn_or_db.execute("SELECT pg_advisory_xact_lock(123456789)")

                await self._apply_sql_file(conn_or_db, filepath)
                await self._record_version(conn_or_db, version)
                applied_names.append(name)
            except Exception as exc:
                raise MigrationError(
                    f"Failed to apply migration {name}: {exc}"
                ) from exc

        if not applied_names:
            logger.debug("No pending migrations for backend %s", self.backend)

        return applied_names

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_schema_version(self, conn_or_db: Any) -> None:
        """Create the ``schema_version`` table if it does not exist."""
        if self.backend == "sqlite":
            await conn_or_db.execute("BEGIN IMMEDIATE")
            await conn_or_db.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "  version INTEGER PRIMARY KEY,"
                "  applied_at TEXT NOT NULL"
                ")"
            )
            await conn_or_db.commit()
        else:
            await conn_or_db.execute("SELECT pg_advisory_xact_lock(123456789)")
            await conn_or_db.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "  version INTEGER PRIMARY KEY,"
                "  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
                ")"
            )

    async def _get_applied_versions(self, conn_or_db: Any) -> set[int]:
        """Return the set of migration version numbers already applied."""
        try:
            if self.backend == "sqlite":
                cursor = await conn_or_db.execute(
                    "SELECT version FROM schema_version"
                )
                rows = await cursor.fetchall()
            else:
                rows = await conn_or_db.fetch("SELECT version FROM schema_version")
            return {row[0] for row in rows}
        except Exception:
            # Table may not exist yet on a brand-new database
            return set()

    def _discover_migrations(self) -> list[tuple[int, Path]]:
        """Return sorted list of ``(version, Path)`` tuples from the
        backend-specific migration directory."""
        migrations: list[tuple[int, Path]] = []
        for child in sorted(self._backend_dir.iterdir()):
            if not child.is_file() or child.suffix != ".sql":
                continue
            version = _parse_version(child.name)
            if version is not None:
                migrations.append((version, child))
        return migrations

    async def _apply_sql_file(self, conn_or_db: Any, filepath: Path) -> None:
        """Execute the SQL statements contained in *filepath*."""
        sql = filepath.read_text(encoding="utf-8")

        if self.backend == "sqlite":
            await conn_or_db.executescript(sql)
            await conn_or_db.commit()
        else:
            # PostgreSQL: split on semicolons and execute each statement
            # (asyncpg does not have executescript)
            statements = [
                stmt.strip()
                for stmt in sql.split(";")
                if stmt.strip()
            ]
            for stmt in statements:
                await conn_or_db.execute(stmt)

    async def _record_version(self, conn_or_db: Any, version: int) -> None:
        """Insert a row into ``schema_version`` recording that *version*
        has been applied."""
        if self.backend == "sqlite":
            await conn_or_db.execute(
                "INSERT INTO schema_version (version, applied_at) "
                "VALUES (?, datetime('now'))",
                (version,),
            )
        else:
            await conn_or_db.execute(
                "INSERT INTO schema_version (version, applied_at) "
                "VALUES ($1, NOW())",
                version,
            )
        if self.backend == "sqlite":
            await conn_or_db.commit()
