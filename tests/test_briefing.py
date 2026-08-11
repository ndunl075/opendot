from datetime import UTC, datetime
from pathlib import Path

from alfred.briefing import BriefingService
from alfred.db import Database
from alfred.events import EventStore
from alfred.tasks import TaskStore


def _add_task(database: Database, title: str, due_at: datetime | None, external_id: str) -> None:
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection,
                source="test",
                external_id=external_id,
                occurred_at=datetime(2026, 8, 10, tzinfo=UTC),
                content=title,
                metadata={},
            )
            TaskStore.create(connection, title=title, source_event_id=event.id, due_at=due_at)


def test_morning_brief_ranks_local_tasks_and_shows_freshness(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    now = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    _add_task(database, "overdue paper", datetime(2026, 8, 13, 9, 0, tzinfo=UTC), "one")
    _add_task(database, "today quiz", datetime(2026, 8, 14, 18, 0, tzinfo=UTC), "two")
    _add_task(database, "next week reading", datetime(2026, 8, 18, 9, 0, tzinfo=UTC), "three")
    _add_task(database, "organize desk", None, "four")

    brief = BriefingService(database).morning_brief(now)

    assert [item.title for item in brief.overdue] == ["overdue paper"]
    assert [item.title for item in brief.due_today] == ["today quiz"]
    assert [item.title for item in brief.upcoming] == ["next week reading"]
    assert [item.title for item in brief.no_due_date] == ["organize desk"]
    assert "Freshness: local Alfred tasks checked 2026-08-14T08:00:00+00:00." in brief.render()


def test_morning_brief_includes_only_current_canvas_missing_assignments(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                INSERT INTO connector_records (connector, account, record_type, record_id, payload_json, observed_at, active)
                VALUES ('canvas', 'self', 'missing', '1', ?, '2026-08-14T07:00:00+00:00', 1),
                       ('canvas', 'self', 'missing', '2', ?, '2026-08-14T07:00:00+00:00', 0)
                """,
                (
                    '{"title":"Missing essay","due_at":"2026-08-10T16:00:00Z","course_name":"Writing","html_url":"https://school.example/1"}',
                    '{"title":"Already submitted","due_at":"2026-08-11T16:00:00Z","course_name":"Writing","html_url":null}',
                ),
            )
    brief = BriefingService(database).morning_brief(datetime(2026, 8, 14, 8, 0, tzinfo=UTC))
    assert [item.title for item in brief.missing_assignments] == ["Missing essay"]
    assert "Canvas missing:\n- Missing essay" in brief.render()
