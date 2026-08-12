from pathlib import Path

from alfred.db import Database


def test_migrate_is_idempotent_and_enables_wal(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")

    assert database.migrate() == 9
    assert database.migrate() == 9

    with database.connect() as connection:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert mode.lower() == "wal"
    assert {
        "events",
        "tool_runs",
        "approvals",
        "jobs",
        "outbox",
        "connector_records",
        "embeddings",
        "action_receipts",
        "documents",
    } <= tables


def test_status_is_non_sensitive(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")

    assert database.status() == {
        "database_path": str(tmp_path / "alfred.db"),
        "schema_version": 9,
        "audit_event_count": 0,
    }


def test_events_allow_repeated_identical_messages(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()

    with database.connect() as connection:
        connection.execute(
            "INSERT INTO events (id, source, occurred_at, content, content_hash) VALUES (?, ?, ?, ?, ?)",
            ("one", "telegram", "2026-08-11T00:00:00Z", "yes", "same-hash"),
        )
        connection.execute(
            "INSERT INTO events (id, source, occurred_at, content, content_hash) VALUES (?, ?, ?, ?, ?)",
            ("two", "telegram", "2026-08-11T00:01:00Z", "yes", "same-hash"),
        )

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
