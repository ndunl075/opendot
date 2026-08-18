"""SQLite connection and migration ownership for Alfred Core."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Iterator

import sqlite_vec


class MigrationConflict(RuntimeError):
    """A packaged migration claims a schema version this database gave another file.

    Raised instead of letting SQLite report a bare ``UNIQUE constraint failed:
    schema_migrations.version``, which says nothing about which two migrations
    collided or what to do about it.
    """


def _migration_version(filename: str) -> int:
    return int(filename.split("_", maxsplit=1)[0])


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
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
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
            recorded = {
                int(row["version"]): str(row["filename"])
                for row in connection.execute("SELECT version, filename FROM schema_migrations")
            }
            applied = set(recorded.values())
            migration_root = files("alfred.migrations")
            migration_files = sorted(
                path for path in migration_root.iterdir() if path.name.endswith(".sql")
            )
            pending = [path for path in migration_files if path.name not in applied]
            # Checked for the whole batch before applying any of it, so a
            # collision on a later file cannot leave earlier ones half-adopted.
            # "Applied" is tracked by filename while the version is the primary
            # key, so a database carrying a migration this build does not ship
            # can hold a version number that one of ours also claims. Without
            # this the only symptom is SQLite's bare UNIQUE constraint message
            # at startup, which names neither file.
            self._require_no_version_conflict(pending, recorded, migration_files)
            for migration in pending:
                version = _migration_version(migration.name)
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

    @staticmethod
    def _require_no_version_conflict(
        pending: list,
        recorded: dict[int, str],
        migration_files: list,
    ) -> None:
        """Refuse to apply a migration whose version this database gave another file."""
        packaged = {path.name for path in migration_files}
        for migration in pending:
            version = _migration_version(migration.name)
            owner = recorded.get(version)
            if owner is None or owner == migration.name:
                continue
            # The migrations this database applied that this build has no file
            # for are the divergence, and naming them turns an unexplained
            # startup crash into a list of rows an operator can act on.
            foreign = sorted(name for name in recorded.values() if name not in packaged)
            raise MigrationConflict(
                f"cannot apply {migration.name}: this database already records schema "
                f"version {version} as {owner}. A version number names exactly one "
                f"migration, so these two histories have diverged and applying this "
                f"file would contradict that record. Nothing has been changed. "
                f"Applied here but absent from this build: "
                f"{', '.join(foreign) if foreign else 'none'}. "
                f"Inspect and repair with: python scripts/reconcile_migrations.py"
            )

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
        """Return non-sensitive local database status for CLI and MCP clients.

        Counts only, never destinations or message text, so this stays safe to
        print and to hand to an MCP client.
        """
        version = self.migrate()
        with self.connect() as connection:
            audit_count = connection.execute("SELECT COUNT(*) AS count FROM tool_runs").fetchone()["count"]
            states = {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    "SELECT state, COUNT(*) AS count FROM outbox GROUP BY state"
                ).fetchall()
            }
            # A row is claimed by moving it to 'sending', and only 'pending'
            # rows are ever claimed, so a process that stops between the claim
            # and the send leaves that row unreachable: no pass retries it, and
            # the agent will not regenerate the answer either, because a stored
            # bubble 0 is what marks the message as already answered. The reply
            # exists, is addressed, and never arrives. Reporting the oldest one
            # is what makes that state findable instead of silent.
            stalled = connection.execute(
                "SELECT created_at FROM outbox WHERE state = 'sending' ORDER BY created_at, rowid LIMIT 1"
            ).fetchone()
            failure = connection.execute(
                """
                SELECT last_error FROM outbox
                WHERE state = 'failed' AND last_error IS NOT NULL
                ORDER BY rowid DESC LIMIT 1
                """
            ).fetchone()
        return {
            "database_path": str(self.path),
            "schema_version": version,
            "audit_event_count": int(audit_count),
            "outbox_pending": states.get("pending", 0),
            "outbox_sending": states.get("sending", 0),
            "outbox_sent": states.get("sent", 0),
            "outbox_failed": states.get("failed", 0),
            "outbox_oldest_unfinished_claim_at": str(stalled["created_at"]) if stalled else "",
            "outbox_last_failure": str(failure["last_error"]) if failure else "",
        }
