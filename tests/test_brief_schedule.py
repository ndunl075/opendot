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


def test_next_daily_occurrence_handles_a_late_run_without_replaying_every_missed_day() -> None:
    assert next_daily_occurrence(
        {"time": "07:30", "timezone": "America/New_York"}, datetime(2026, 8, 17, 18, 0, tzinfo=UTC)
    ) == datetime(2026, 8, 18, 11, 30, tzinfo=UTC)
