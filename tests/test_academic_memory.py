import json
from datetime import UTC, datetime
from pathlib import Path

from alfred.academic_memory import AcademicMemoryService
from alfred.db import Database
from alfred.events import EventStore
from alfred.hermes_bridge import AgentRunResult, HermesBridge


def _calendar_event(connection, *, event_id: str, title: str, start: str, updated: str) -> None:
    EventStore.append(
        connection,
        source="google_calendar",
        external_id=f"{event_id}:{updated}",
        occurred_at=datetime.fromisoformat(updated.replace("Z", "+00:00")),
        content=title,
        metadata={
            "calendar_id": "school@example.com",
            "calendar_event_id": event_id,
            "status": "confirmed",
            "start": start,
            "end": start,
            "html_link": None,
        },
    )


def test_rollups_dedupe_versions_and_connect_items_by_calendar(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                INSERT INTO connector_records (
                    connector, account, record_type, record_id,
                    payload_json, observed_at, active
                ) VALUES ('google_calendar', 'self', 'calendar', 'school@example.com', ?, ?, 1)
                """,
                (
                    json.dumps({"id": "school@example.com", "title": "Calculus", "primary": False}),
                    datetime.now(UTC).isoformat(),
                ),
            )
            _calendar_event(
                connection,
                event_id="exam-1",
                title="Midterm draft",
                start="2026-08-12T10:00:00-04:00",
                updated="2026-08-01T12:00:00Z",
            )
            _calendar_event(
                connection,
                event_id="exam-1",
                title="Calculus Midterm",
                start="2026-08-13T10:00:00-04:00",
                updated="2026-08-02T12:00:00Z",
            )

    result = AcademicMemoryService(database).rebuild_if_changed()

    assert result.changed is True
    assert (result.source_events, result.items, result.days, result.groups) == (2, 1, 1, 1)
    found = AcademicMemoryService(database).search("calculus test")
    assert found.groups[0]["label"] == "Calculus"
    assert found.groups[0]["stats"]["types"] == {"exam": 1}
    assert found.days[0]["items"][0]["title"] == "Calculus Midterm"
    assert found.days[0]["items"][0]["source_event_id"]


def test_rollup_is_a_noop_until_source_history_changes(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    service = AcademicMemoryService(database)

    first = service.rebuild_if_changed()
    second = service.rebuild_if_changed()

    assert first.changed is True
    assert second.changed is False


def test_canvas_assignments_are_grouped_by_course(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            EventStore.append(
                connection,
                source="canvas",
                external_id="upcoming:42:2026-08-10T12:00:00Z",
                occurred_at=datetime(2026, 8, 10, 12, tzinfo=UTC),
                content="Problem Set 3",
                metadata={
                    "assignment_id": "42",
                    "kind": "upcoming",
                    "due_at": "2026-08-14T23:59:00-04:00",
                    "course_name": "MATH 1151",
                    "html_url": None,
                },
            )

    AcademicMemoryService(database).rebuild_if_changed()
    found = AcademicMemoryService(database).search("math homework")

    assert found.groups[0]["label"] == "MATH 1151"
    assert found.groups[0]["stats"]["types"] == {"assignment": 1}

    bridge = HermesBridge(
        database, lambda prompt: AgentRunResult(text="unused", ok=True)
    )
    prompt = bridge._agent_prompt(
        {
            "content": "what assignments have I had in math?",
            "external_id": "academic-test",
            "chat_id": 20,
            "occurred_at": datetime.now(UTC).isoformat(),
        }
    )
    assert '"academic_history"' in prompt
    assert "Problem Set 3" in prompt


def test_rollups_prefer_canvas_evidence_over_an_identical_google_calendar_copy(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            _calendar_event(
                connection,
                event_id="google-copy",
                title="Project 1 [CSE 2231]",
                start="2026-08-14T22:00:00Z",
                updated="2026-08-01T12:00:00Z",
            )
            EventStore.append(
                connection,
                source="canvas",
                external_id="canvas-ical:canvas-copy:v1",
                occurred_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
                content="Project 1 [CSE 2231]",
                metadata={
                    "assignment_id": "canvas-copy",
                    "kind": "assignment",
                    "due_at": "2026-08-14T18:00:00-04:00",
                    "course_name": "CSE 2231",
                    "html_url": None,
                    "source_connector": "canvas_ical",
                },
            )

    result = AcademicMemoryService(database).rebuild_if_changed()
    found = AcademicMemoryService(database).search("Project 1")

    assert result.source_events == 2
    assert result.items == result.groups == 1
    assert found.groups[0]["label"] == "CSE 2231"
    assert found.days[0]["items"][0]["stable_key"] == "canvas:canvas-copy"
