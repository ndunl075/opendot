import json
from datetime import UTC, datetime, time
from pathlib import Path

from alfred.brief_schedule import create_daily, next_daily_occurrence
from alfred.db import Database
from alfred.jobs import JobRunner


def test_daily_schedule_uses_wall_clock_timezone_and_recovers_to_next_run(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            job_id = create_daily(
                connection,
                chat_id=20,
                local_time=time(7, 30),
                timezone_name="America/New_York",
                now=datetime(2026, 8, 14, 11, 0, tzinfo=UTC),
            )

    executed = JobRunner(database).run_due(datetime(2026, 8, 14, 12, 0, tzinfo=UTC))

    assert len(executed) == 1
    with database.connect() as connection:
        job = connection.execute("SELECT state, next_run_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
        message = json.loads(connection.execute("SELECT payload_json FROM outbox").fetchone()[0])
    assert job["state"] == "active"
    assert datetime.fromisoformat(job["next_run_at"]) == datetime(2026, 8, 15, 11, 30, tzinfo=UTC)
    assert message["text"].startswith("Morning brief")
    assert "Note: delivered late (scheduled 2026-08-14T07:30:00-04:00" in message["text"]


def test_on_time_daily_brief_carries_no_late_note(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            create_daily(
                connection,
                chat_id=20,
                local_time=time(7, 30),
                timezone_name="America/New_York",
                now=datetime(2026, 8, 14, 11, 0, tzinfo=UTC),
            )

    executed = JobRunner(Database(database.path)).run_due(datetime(2026, 8, 14, 11, 30, tzinfo=UTC))

    assert executed[0].late is False
    with database.connect() as connection:
        message = json.loads(connection.execute("SELECT payload_json FROM outbox").fetchone()[0])
    assert "Note: delivered late" not in message["text"]


def test_daily_schedule_keeps_an_explicit_destination(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            job_id = create_daily(
                connection,
                destination="slack:D123",
                local_time=time(7, 30),
                timezone_name="America/New_York",
                now=datetime(2026, 8, 14, 11, 0, tzinfo=UTC),
            )

    JobRunner(database).run_due(datetime(2026, 8, 14, 11, 30, tzinfo=UTC))

    with database.connect() as connection:
        destination = connection.execute("SELECT destination FROM outbox WHERE job_id = ?", (job_id,)).fetchone()[0]
    assert destination == "slack:D123"


def test_next_daily_occurrence_handles_a_late_run_without_replaying_every_missed_day() -> None:
    assert next_daily_occurrence(
        {"time": "07:30", "timezone": "America/New_York"}, datetime(2026, 8, 17, 18, 0, tzinfo=UTC)
    ) == datetime(2026, 8, 18, 11, 30, tzinfo=UTC)
