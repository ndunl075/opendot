"""Daily fixed-time reminders: wake-up, bedtime, study lock-in."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alfred.db import Database
from alfred.events import EventStore
from alfred.hermes_tools import (
    is_casual_conversation,
    select_hermes_tools,
    wants_scheduling,
)
from alfred.jobs import JobRunner
from alfred.reminders import ReminderStore
from alfred.tasks import TaskStore


def _create_daily_reminder(
    database: Database,
    *,
    text: str,
    run_at: datetime,
    timezone_name: str,
    destination: str = "telegram:20",
    idempotency_key: str = "daily-reminder",
) -> str:
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection,
                source="test",
                external_id=idempotency_key,
                occurred_at=run_at,
                content=text,
                metadata={},
            )
            task = TaskStore.create(connection, title=text, source_event_id=event.id)
            job = ReminderStore.create(
                connection,
                run_at=run_at,
                task_id=task.id,
                destination=destination,
                text=text,
                daily=True,
                timezone_name=timezone_name,
                idempotency_key=idempotency_key,
            )
    return job.id


def test_daily_reminder_reschedules_to_the_next_local_wall_clock(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    # 07:00 America/New_York on Aug 14 2026 is 11:00 UTC (EDT).
    job_id = _create_daily_reminder(
        database,
        text="Wake up",
        run_at=datetime(2026, 8, 14, 11, 0, tzinfo=UTC),
        timezone_name="America/New_York",
    )

    executed = JobRunner(database).run_due(datetime(2026, 8, 14, 11, 0, tzinfo=UTC))

    assert len(executed) == 1
    assert executed[0].late is False
    with database.connect() as connection:
        job = connection.execute("SELECT state, next_run_at, schedule_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
        message = json.loads(connection.execute("SELECT payload_json FROM outbox WHERE job_id = ?", (job_id,)).fetchone()[0])
    assert job["state"] == "active"
    assert datetime.fromisoformat(job["next_run_at"]) == datetime(2026, 8, 15, 11, 0, tzinfo=UTC)
    assert json.loads(job["schedule_json"])["time"] == "07:00"
    assert message["text"] == "Reminder: Wake up"


def test_late_daily_reminder_skips_missed_days_without_replaying_them(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    job_id = _create_daily_reminder(
        database,
        text="Bedtime",
        run_at=datetime(2026, 8, 14, 3, 0, tzinfo=UTC),  # 23:00 EDT Aug 13
        timezone_name="America/New_York",
        idempotency_key="bedtime",
    )

    # Fire three days late: next occurrence is the next local 23:00, not a backlog.
    JobRunner(database).run_due(datetime(2026, 8, 17, 18, 0, tzinfo=UTC))

    with database.connect() as connection:
        job = connection.execute("SELECT state, next_run_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
        message = json.loads(connection.execute("SELECT payload_json FROM outbox WHERE job_id = ?", (job_id,)).fetchone()[0])
        outbox_count = connection.execute("SELECT COUNT(*) FROM outbox WHERE job_id = ?", (job_id,)).fetchone()[0]
    assert job["state"] == "active"
    assert outbox_count == 1
    assert "Late reminder" in message["text"]
    # After a late run at 18:00 UTC Aug 17, next local 23:00 EDT is still Aug 17 03:00 UTC next day... 
    # Aug 17 18:00 UTC = Aug 17 14:00 EDT. Next 23:00 EDT is Aug 17 23:00 EDT = Aug 18 03:00 UTC.
    assert datetime.fromisoformat(job["next_run_at"]) == datetime(2026, 8, 18, 3, 0, tzinfo=UTC)


def test_daily_reminder_without_a_timezone_is_refused(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection,
                source="test",
                external_id="no-zone",
                occurred_at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
                content="Lock in",
                metadata={},
            )
            task = TaskStore.create(connection, title="Lock in", source_event_id=event.id)
            with pytest.raises(ValueError, match="IANA timezone"):
                ReminderStore.create(
                    connection,
                    run_at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
                    task_id=task.id,
                    destination="telegram:20",
                    text="Study lock-in",
                    daily=True,
                    idempotency_key="no-zone",
                )


def test_one_shot_reminder_still_completes(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection,
                source="test",
                external_id="once",
                occurred_at=datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
                content="Once",
                metadata={},
            )
            task = TaskStore.create(connection, title="Once", source_event_id=event.id)
            job = ReminderStore.create(
                connection,
                run_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
                task_id=task.id,
                destination="telegram:20",
                text="Once",
                idempotency_key="once",
            )

    JobRunner(database).run_due(datetime(2026, 8, 14, 9, 0, tzinfo=UTC))

    with database.connect() as connection:
        state = connection.execute("SELECT state, next_run_at FROM jobs WHERE id = ?", (job.id,)).fetchone()
    assert state["state"] == "completed"
    assert state["next_run_at"] is None


def test_wake_bedtime_and_lock_in_phrases_select_reminder_tools() -> None:
    for phrase in (
        "wake me up at 7 every day",
        "remind me at bedtime",
        "study lock-in at 8pm each night",
        "set a daily reminder to lock in",
    ):
        assert wants_scheduling(phrase)
        tools = select_hermes_tools(phrase)
        assert "reminder_set" in tools
        assert not is_casual_conversation(phrase)


def test_reminding_is_not_trapped_by_a_word_boundary() -> None:
    # ``\bremind\b`` never matches "reminding"; keep that class of bug closed.
    assert wants_scheduling("keep reminding me to stretch every morning")
    assert "reminder_set" in select_hermes_tools("keep reminding me to stretch every morning")
