from importlib.resources import files
from pathlib import Path

import pytest

from alfred.db import Database, MigrationConflict


def test_migrate_is_idempotent_and_enables_wal(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")

    assert database.migrate() == 17
    assert database.migrate() == 17

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
        "mood_entries",
        "gratitude_entries",
    } <= tables


def test_migrate_refuses_a_version_another_migration_already_recorded(tmp_path: Path) -> None:
    """A diverged history must say what collided, not surface a UNIQUE constraint.

    "Applied" is tracked by filename while the version is the primary key, so a
    database carrying a migration this build does not ship can hold a version
    one of ours also claims. Reported as its own error because the operator has
    to choose which history wins; SQLite's own message names neither file.
    """
    database = Database(tmp_path / "alfred.db")
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                filename TEXT NOT NULL UNIQUE,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations (version, filename) VALUES (16, '0016_habits.sql')"
        )

    with pytest.raises(MigrationConflict) as error:
        database.migrate()

    message = str(error.value)
    assert "0016_journal.sql" in message
    assert "0016_habits.sql" in message

    # The check covers the whole pending batch, so the fifteen migrations that
    # would have applied cleanly before reaching the collision must not have
    # run either: a refusal that half-migrates is worse than the crash it
    # replaces.
    with database.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "events" not in tables


def test_migrate_allows_a_recorded_migration_this_build_does_not_ship(tmp_path: Path) -> None:
    """Divergence alone is not a conflict; only a contested version number is."""
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO schema_migrations (version, filename) VALUES (94, '0094_local_only.sql')"
        )

    assert database.migrate() == 94


def test_status_is_non_sensitive(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")

    assert database.status() == {
        "database_path": str(tmp_path / "alfred.db"),
        "schema_version": 17,
        "audit_event_count": 0,
        "outbox_pending": 0,
        "outbox_sending": 0,
        "outbox_sent": 0,
        "outbox_failed": 0,
        "outbox_oldest_unfinished_claim_at": "",
        "outbox_last_failure": "",
    }


def test_status_surfaces_a_reply_stranded_by_an_unfinished_claim(tmp_path: Path) -> None:
    """A claimed row nothing will retry is the one delivery failure with no symptom.

    It is not pending, so no delivery pass sees it, and its stored bubble 0
    tells the agent the message was already answered. Without this the only
    evidence is a chat that stopped replying.
    """
    database = Database(tmp_path / "alfred.db")
    database.migrate()

    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                INSERT INTO outbox (id, destination, payload_json, idempotency_key, state, attempts, created_at)
                VALUES ('a', 'telegram:1', '{"text":"hi"}', 'hermes-reply:900:0', 'sending', 1, '2026-08-16 21:00:00')
                """
            )
            connection.execute(
                """
                INSERT INTO outbox (id, destination, payload_json, idempotency_key, state, attempts, created_at, last_error)
                VALUES ('b', 'telegram:1', '{"text":"yo"}', 'hermes-reply:901:0', 'failed', 1, '2026-08-16 22:00:00',
                        'Telegram send failed: ReadTimeout')
                """
            )

    status = database.status()

    assert status["outbox_sending"] == 1
    assert status["outbox_oldest_unfinished_claim_at"] == "2026-08-16 21:00:00"
    assert status["outbox_failed"] == 1
    assert status["outbox_last_failure"] == "Telegram send failed: ReadTimeout"
def test_rebuilding_a_table_keeps_the_rows_it_already_had(tmp_path: Path) -> None:
    """Upgrade an existing install, not a fresh one.

    A fresh database exercises every migration together, so it can never catch
    a rebuild that drops live rows. 0017 recreates ``response_feedback`` to
    relax constraints SQLite cannot alter in place, and the votes an install
    already collected have to come through it labeled as the button taps they
    were.
    """
    database = Database(tmp_path / "alfred.db")
    migrations = sorted(
        path for path in files("alfred.migrations").iterdir() if path.name.endswith(".sql")
    )
    with database.connect() as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
            "filename TEXT NOT NULL UNIQUE, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        for migration in migrations:
            if migration.name.startswith("0017"):
                break
            version = int(migration.name.split("_", maxsplit=1)[0])
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{migration.read_text(encoding='utf-8')}\n"
                "INSERT INTO schema_migrations (version, filename) "
                f"VALUES ({version}, '{migration.name}');\nCOMMIT;"
            )
        connection.execute(
            "INSERT INTO response_context (response_update_id, created_at) VALUES ('7', ?)",
            ("2026-08-01T00:00:00+00:00",),
        )
        connection.execute(
            "INSERT INTO response_feedback (id, callback_query_id, feedback_update_id, "
            "response_update_id, outcome, created_at) VALUES ('a', 'cb-1', '8', '7', 'helpful', ?)",
            ("2026-08-01T00:00:01+00:00",),
        )
        connection.commit()

    assert database.migrate() == 17

    with database.connect() as connection:
        row = connection.execute(
            "SELECT signal, outcome, rule FROM response_feedback WHERE id = 'a'"
        ).fetchone()

    assert (row["signal"], row["outcome"], row["rule"]) == ("button", "helpful", None)


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
