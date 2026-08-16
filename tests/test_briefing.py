import json
from datetime import UTC, datetime
from pathlib import Path

from alfred.audit import AuditLog
from alfred.briefing import BriefingService
from alfred.db import Database
from alfred.events import EventStore
from alfred.models import GenerationResult
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


def test_morning_brief_includes_native_canvas_ical_assignments(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                INSERT INTO connector_records (
                    connector, account, record_type, record_id,
                    payload_json, observed_at, active
                ) VALUES ('canvas_ical', 'self', 'assignment', '1', ?, ?, 1)
                """,
                (
                    '{"title":"Project 1","due_at":"2026-08-16T23:59:00-04:00","course_name":"CSE 2231","html_url":null}',
                    "2026-08-14T07:00:00+00:00",
                ),
            )

    brief = BriefingService(database).morning_brief(datetime(2026, 8, 14, 8, 0, tzinfo=UTC))

    assert [item.title for item in brief.upcoming] == ["Project 1"]


def test_morning_brief_suppresses_a_google_calendar_copy_of_a_canvas_assignment(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    assignment = json.dumps(
        {
            "title": "Project 1 [CSE 2231]",
            "due_at": "2026-08-14T18:00:00-04:00",
            "course_name": "CSE 2231",
            "html_url": None,
        }
    )
    calendar_copy = json.dumps(
        {
            "title": "Project 1 [CSE 2231]",
            "start": "2026-08-14T22:00:00Z",
            "end": "2026-08-14T22:30:00Z",
            "html_url": None,
        }
    )
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                INSERT INTO connector_records (
                    connector, account, record_type, record_id,
                    payload_json, observed_at, active
                ) VALUES ('canvas_ical', 'self', 'assignment', 'canvas-copy', ?, ?, 1),
                         ('google_calendar', 'canvas-calendar', 'event', 'google-copy', ?, ?, 1)
                """,
                (assignment, "2026-08-14T07:00:00Z", calendar_copy, "2026-08-14T07:00:00Z"),
            )

    brief = BriefingService(database).morning_brief(datetime(2026, 8, 14, 8, 0, tzinfo=UTC))

    assert [item.title for item in brief.due_today] == ["Project 1 [CSE 2231]"]
    assert brief.calendar_today == []


def test_morning_brief_includes_current_calendar_events_today(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                INSERT INTO connector_records (connector, account, record_type, record_id, payload_json, observed_at, active)
                VALUES ('google_calendar', 'primary', 'event', 'one', ?, '2026-08-14T07:00:00+00:00', 1)
                """,
                ('{"title":"Advisor meeting","start":"2026-08-14T14:00:00Z","end":"2026-08-14T14:30:00Z","html_url":"https://calendar.example/event"}',),
            )
    brief = BriefingService(database).morning_brief(datetime(2026, 8, 14, 8, 0, tzinfo=UTC))
    assert [item.title for item in brief.calendar_today] == ["Advisor meeting"]
    assert "Today's calendar:\n- Advisor meeting" in brief.render()


def test_morning_brief_uses_local_day_and_identifies_calendar_conflicts(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                INSERT INTO connector_records (connector, account, record_type, record_id, payload_json, observed_at, active)
                VALUES ('google_calendar', 'primary', 'event', 'one', ?, '2026-08-14T07:00:00+00:00', 1),
                       ('google_calendar', 'primary', 'event', 'two', ?, '2026-08-14T07:00:00+00:00', 1)
                """,
                (
                    '{"title":"Late study","start":"2026-08-15T03:30:00Z","end":"2026-08-15T04:30:00Z"}',
                    '{"title":"Project call","start":"2026-08-15T03:45:00Z","end":"2026-08-15T04:00:00Z"}',
                ),
            )

    brief = BriefingService(database).morning_brief(
        datetime(2026, 8, 14, 12, 0, tzinfo=UTC), timezone_name="America/New_York"
    )

    assert [item.title for item in brief.calendar_today] == ["Late study", "Project call"]
    assert brief.conflicts == ["Late study overlaps Project call"]
    assert "Calendar conflicts:" in brief.render()


def test_morning_brief_includes_only_active_github_notifications(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                INSERT INTO connector_records (connector, account, record_type, record_id, payload_json, observed_at, active)
                VALUES ('github', 'self', 'notification', '1', ?, '2026-08-14T07:00:00+00:00', 1),
                       ('github', 'self', 'notification', '2', ?, '2026-08-14T07:00:00+00:00', 0)
                """,
                (
                    '{"title":"Fix flaky test","repo":"example/alfred","html_url":"https://github.com/example/alfred/pull/42"}',
                    '{"title":"Stale review request","repo":"example/alfred","html_url":null}',
                ),
            )
    brief = BriefingService(database).morning_brief(datetime(2026, 8, 14, 8, 0, tzinfo=UTC))
    assert [item.title for item in brief.github_notifications] == ["example/alfred: Fix flaky test"]
    assert "GitHub notifications:\n- example/alfred: Fix flaky test" in brief.render()


class _FakeWriter:
    model_name = "fake"

    def __init__(self, *, text: str = "Good morning! Nothing due today.") -> None:
        self._text = text
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, system: str | None = None) -> GenerationResult:
        self.prompts.append(prompt)
        return GenerationResult(text=self._text, model=self.model_name, prompt_tokens=10, completion_tokens=5)


class _BrokenWriter:
    model_name = "broken"

    def generate(self, prompt: str, *, system: str | None = None) -> GenerationResult:
        raise RuntimeError("Ollama is not running")


def test_write_brief_without_a_writer_is_the_deterministic_render(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    service = BriefingService(database)
    brief = service.morning_brief(datetime(2026, 8, 14, 8, 0, tzinfo=UTC))

    assert service.write_brief(brief) == brief.render()


def test_write_brief_asks_the_model_and_grounds_the_prompt_in_the_deterministic_text(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    writer = _FakeWriter()
    service = BriefingService(database, llm_writer=writer)
    brief = service.morning_brief(datetime(2026, 8, 14, 8, 0, tzinfo=UTC))

    text = service.write_brief(brief)

    assert text == "Good morning! Nothing due today."
    assert brief.render() in writer.prompts[0]  # the model only ever sees the deterministic facts
    with database.connect() as connection:
        row = connection.execute("SELECT tool, outcome, result_json FROM tool_runs").fetchone()
    assert row["tool"] == "brief_llm_pass"
    assert row["outcome"] == "ok"
    assert '"prompt_tokens":10' in row["result_json"]
    assert AuditLog(database).verify() is True


def test_write_brief_falls_back_to_the_deterministic_render_on_a_model_failure(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    service = BriefingService(database, llm_writer=_BrokenWriter())
    brief = service.morning_brief(datetime(2026, 8, 14, 8, 0, tzinfo=UTC))

    text = service.write_brief(brief)

    assert text == brief.render()
    with database.connect() as connection:
        row = connection.execute("SELECT tool, outcome, result_json FROM tool_runs").fetchone()
    assert row["tool"] == "brief_llm_pass"
    assert row["outcome"] == "error"
    assert "Ollama is not running" in row["result_json"]


def test_morning_brief_includes_last_night_sleep_from_google_health(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    payload = {
        "data_type": "sleep",
        "raw": {
            "interval": {
                "startTime": "2026-08-13T23:00:00Z",
                "endTime": "2026-08-14T06:30:00Z",
            },
            "sleep": {"stage": "deep"},
        },
    }
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                INSERT INTO connector_records (
                    connector, account, record_type, record_id,
                    payload_json, observed_at, active
                ) VALUES ('google_health', 'self', 'sleep', 'sleep:1', ?, '2026-08-14T07:00:00+00:00', 1)
                """,
                (json.dumps(payload),),
            )

    brief = BriefingService(database).morning_brief(datetime(2026, 8, 14, 8, 0, tzinfo=UTC))

    assert brief.sleep_summary == "7h 30m last night — Google Health (includes deep)"
    assert "Sleep:\n- 7h 30m last night — Google Health (includes deep)" in brief.render()


def test_morning_brief_omits_sleep_when_google_health_has_no_night_data(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    # Daytime nap outside the evening→noon window must not invent "last night".
    payload = {
        "data_type": "sleep",
        "raw": {
            "interval": {
                "startTime": "2026-08-13T14:00:00Z",
                "endTime": "2026-08-13T15:00:00Z",
            },
            "sleep": {"stage": "light"},
        },
    }
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                INSERT INTO connector_records (
                    connector, account, record_type, record_id,
                    payload_json, observed_at, active
                ) VALUES ('google_health', 'self', 'sleep', 'sleep:nap', ?, '2026-08-14T07:00:00+00:00', 1)
                """,
                (json.dumps(payload),),
            )

    brief = BriefingService(database).morning_brief(datetime(2026, 8, 14, 8, 0, tzinfo=UTC))

    assert brief.sleep_summary is None
    assert "Sleep:" not in brief.render()
