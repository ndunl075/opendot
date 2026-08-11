"""SQLite connection and migration ownership for Alfred Core."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Iterator


class Database:
    """A single-process SQLite owner with explicit migrations and transactions."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        """Open a connection configured for durable local use."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def migrate(self) -> int:
        """Apply each packaged SQL migration exactly once and return the schema version."""
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    filename TEXT NOT NULL UNIQUE,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            applied = {
                row["filename"]
                for row in connection.execute("SELECT filename FROM schema_migrations")
            }
            migration_root = files("alfred.migrations")
            migration_files = sorted(
                path for path in migration_root.iterdir() if path.name.endswith(".sql")
            )
            for migration in migration_files:
                if migration.name in applied:
                    continue
                version = int(migration.name.split("_", maxsplit=1)[0])
                script = migration.read_text(encoding="utf-8")
                filename_literal = migration.name.replace("'", "''")
                # ``executescript`` commits an already-open transaction, so the
                # migration's transaction must live inside the script itself.
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    f"{script}\n"
                    "INSERT INTO schema_migrations (version, filename) "
                    f"VALUES ({version}, '{filename_literal}');\n"
                    "COMMIT;"
                )
            row = connection.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations").fetchone()
            return int(row["version"])

    @contextmanager
    def transaction(self, connection: sqlite3.Connection) -> Iterator[None]:
        """Use an immediate transaction so a writer cannot observe a partial action."""
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()

    def status(self) -> dict[str, int | str]:
        """Return non-sensitive local database status for CLI and MCP clients."""
        version = self.migrate()
        with self.connect() as connection:
            audit_count = connection.execute("SELECT COUNT(*) AS count FROM tool_runs").fetchone()["count"]
        return {
            "database_path": str(self.path),
            "schema_version": version,
            "audit_event_count": int(audit_count),
        }
