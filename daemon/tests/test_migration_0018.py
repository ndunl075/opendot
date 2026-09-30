from importlib.resources import files
from pathlib import Path

from opendot_core.db import Database

NOW = "2026-08-01T00:00:00+00:00"


def _migrate_up_to_0017(database: Database) -> None:
    migrations = sorted(
        path for path in files("opendot_core.migrations").iterdir() if path.name.endswith(".sql")
    )
    with database.connect() as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
            "filename TEXT NOT NULL UNIQUE, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        for migration in migrations:
            if migration.name.startswith("0018"):
                break
            version = int(migration.name.split("_", maxsplit=1)[0])
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{migration.read_text(encoding='utf-8')}\n"
                "INSERT INTO schema_migrations (version, filename) "
                f"VALUES ({version}, '{migration.name}');\nCOMMIT;"
            )


def _event(connection, event_id: str, source: str) -> None:
    connection.execute(
        "INSERT INTO events (id, source, external_id, occurred_at, content, metadata_json, content_hash) "
        "VALUES (?, ?, ?, ?, 'item', '{}', ?)",
        (event_id, source, f"{source}-1", NOW, f"hash-{event_id}"),
    )


def _memory(connection, memory_id: str, event_id: str) -> None:
    connection.execute(
        "INSERT INTO memories (id, kind, statement, status, source_event_id, created_at, updated_at) "
        "VALUES (?, 'history', ?, 'confirmed', ?, ?, ?)",
        (memory_id, f"statement {memory_id}", event_id, NOW, NOW),
    )
    connection.execute("INSERT INTO memory_fts (memory_id, statement) VALUES (?, ?)", (memory_id, f"statement {memory_id}"))
    connection.execute(
        "INSERT INTO evidence (id, subject_kind, subject_id, source_event_id, created_at) "
        "VALUES (?, 'memory', ?, ?, ?)",
        (f"ev-{memory_id}", memory_id, event_id, NOW),
    )
    connection.execute(
        "INSERT INTO historical_memory_items (stable_key, source_fingerprint, source_event_id, memory_id, updated_at) "
        "VALUES (?, 'fp', ?, ?, ?)",
        (f"{memory_id}-key", event_id, memory_id, NOW),
    )


def test_migration_renames_rollups_and_purges_removed_connector_data(tmp_path: Path) -> None:
    database = Database(tmp_path / "opendot.db")
    _migrate_up_to_0017(database)
    with database.connect() as connection:
        for event_id, source in (("e-canvas", "canvas"), ("e-health", "google_health"), ("e-cal", "google_calendar")):
            _event(connection, event_id, source)
        _memory(connection, "m-canvas", "e-canvas")
        _memory(connection, "m-cal", "e-cal")
        connection.execute(
            "INSERT INTO embeddings (id, subject_kind, subject_id, model_name, dim, vector, created_at) "
            "VALUES ('v1', 'memory', 'm-canvas', 'fake', 1, x'00', ?)",
            (NOW,),
        )
        connection.execute(
            "INSERT INTO connector_records (connector, account, record_type, record_id, payload_json, observed_at) "
            "VALUES ('canvas_ical', 'self', 'assignment', '1', '{}', ?), "
            "('google_calendar', 'self', 'event', '1', '{}', ?)",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO sync_state (connector, account, updated_at) VALUES "
            "('canvas', 'self', ?), ('google_health', 'self', ?), ('github', 'self', ?)",
            (NOW, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO academic_rollup_state (singleton, source_fingerprint, source_event_count, generated_at) "
            "VALUES (1, 'fp', 3, ?)",
            (NOW,),
        )
        connection.execute(
            "INSERT INTO academic_group_rollups (group_key, group_label, first_day, last_day, stats_json, search_text, generated_at) "
            "VALUES ('canvas:math', 'MATH', '2026-08-01', '2026-08-02', '{}', 'math', ?)",
            (NOW,),
        )
        connection.commit()

    assert database.migrate() == 18

    with database.connect() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"calendar_daily_rollups", "calendar_group_rollups", "calendar_rollup_state"} <= tables
        assert not {name for name in tables if name.startswith("academic_")}
        assert connection.execute("SELECT COUNT(*) FROM calendar_group_rollups").fetchone()[0] == 0
        assert {row[0] for row in connection.execute("SELECT id FROM events")} == {"e-cal"}
        assert {row[0] for row in connection.execute("SELECT id FROM memories")} == {"m-cal"}
        assert {row[0] for row in connection.execute("SELECT memory_id FROM memory_fts")} == {"m-cal"}
        assert connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 0
        assert {row[0] for row in connection.execute("SELECT stable_key FROM historical_memory_items")} == {"m-cal-key"}
        assert {row[0] for row in connection.execute("SELECT connector FROM connector_records")} == {"google_calendar"}
        assert {row[0] for row in connection.execute("SELECT connector FROM sync_state")} == {"github"}
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_is_a_noop_for_a_database_with_no_removed_connector_data(tmp_path: Path) -> None:
    database = Database(tmp_path / "opendot.db")

    assert database.migrate() == 18
    assert database.migrate() == 18
