import json
from datetime import UTC, datetime
from pathlib import Path

from alfred.academic_memory import AcademicMemoryService
from alfred.db import Database
from alfred.events import EventStore
from alfred.historical_memory import HistoricalMemoryService
from alfred.memory_graph import MemoryGraph


def _event(database: Database, *, title: str, updated: str, start: str) -> None:
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                INSERT INTO connector_records (
                    connector, account, record_type, record_id, payload_json, observed_at, active
                ) VALUES ('google_calendar', 'self', 'calendar', 'school', ?, ?, 1)
                ON CONFLICT(connector, account, record_type, record_id) DO UPDATE SET
                    payload_json = excluded.payload_json, observed_at = excluded.observed_at, active = 1
                """,
                (json.dumps({"id": "school", "title": "Calculus", "primary": False}), datetime.now(UTC).isoformat()),
            )
            EventStore.append(
                connection,
                source="google_calendar",
                external_id=f"exam:{updated}",
                occurred_at=datetime.fromisoformat(updated.replace("Z", "+00:00")),
                content=title,
                metadata={
                    "calendar_id": "school",
                    "calendar_event_id": "exam",
                    "status": "confirmed",
                    "start": start,
                    "end": start,
                    "creator": {"displayName": "Professor Ada"},
                },
            )


def test_history_becomes_idempotent_provenance_linked_memory(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _event(database, title="Calculus Midterm", updated="2026-08-01T12:00:00Z", start="2026-08-13T10:00:00-04:00")
    AcademicMemoryService(database).rebuild_if_changed()

    first = HistoricalMemoryService(database).rebuild_if_changed()
    second = HistoricalMemoryService(database).rebuild_if_changed()

    assert first.changed is True
    assert (first.groups_created, first.memories_created, first.active_items) == (1, 1, 1)
    assert second.changed is False
    found = MemoryGraph(database).search("Calculus Midterm Professor Ada")
    assert len(found.memories) == 1
    assert "added by Professor Ada" in found.memories[0].statement
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM evidence WHERE subject_kind = 'memory'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 2  # owner + calendar
        assert connection.execute("SELECT predicate FROM relationships").fetchone()[0] == "uses_calendar"


def test_new_provider_version_supersedes_old_derived_memory(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _event(database, title="Midterm draft", updated="2026-08-01T12:00:00Z", start="2026-08-13T10:00:00-04:00")
    AcademicMemoryService(database).rebuild_if_changed()
    HistoricalMemoryService(database).rebuild_if_changed()

    _event(database, title="Calculus Midterm", updated="2026-08-02T12:00:00Z", start="2026-08-14T10:00:00-04:00")
    AcademicMemoryService(database).rebuild_if_changed()
    result = HistoricalMemoryService(database).rebuild_if_changed()

    assert result.memories_updated == 1
    with database.connect() as connection:
        rows = connection.execute("SELECT statement, status FROM memories ORDER BY created_at, rowid").fetchall()
    assert [row["status"] for row in rows] == ["superseded", "confirmed"]
    assert "Calculus Midterm" in rows[1]["statement"]


def test_rebuild_cleans_vectors_for_superseded_derived_memories(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    memory = MemoryGraph(database).remember("old history", kind="history")
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute("UPDATE memories SET status = 'superseded' WHERE id = ?", (memory.id,))
            connection.execute(
                "INSERT INTO embeddings (id, subject_kind, subject_id, model_name, dim, vector, created_at) VALUES ('v', 'memory', ?, 'fake', 1, ?, ?)",
                (memory.id, b"1234", datetime.now(UTC).isoformat()),
            )

    HistoricalMemoryService(database).rebuild_if_changed()

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM embeddings WHERE subject_id = ?", (memory.id,)).fetchone()[0] == 0
