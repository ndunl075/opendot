"""Nag-until-done: repeating reminders tied to open tasks."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alfred.db import Database
from alfred.events import EventStore
from alfred.hermes_tools import select_hermes_tools
from alfred.jobs import JobRunner
from alfred.nags import NagStore
from alfred.tasks import TaskStore


def _create_nag(
    database: Database,
    *,
    text: str = "Submit the paper",
    run_at: datetime | None = None,
    interval_hours: float = 1.0,
    max_attempts: int = 3,
    destination: str = "telegram:20",
    idempotency_key: str = "nag-1",
) -> tuple[str, str]:
    database.migrate()
    first_run = run_at or datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection,
                source="test",
                external_id=idempotency_key,
                occurred_at=first_run,
                content=text,
                metadata={},
            )
            task = TaskStore.create(connection, title=text, source_event_id=event.id)
            job = NagStore.create(
                connection,
                run_at=first_run,
                task_id=task.id,
                destination=destination,
                text=text,
                interval_hours=interval_hours,
                max_attempts=max_attempts,
                idempotency_key=idempotency_key,
            )
    return job.id, task.id


def test_nag_delivers_and_reschedules_while_task_is_open(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    job_id, _task_id = _create_nag(database, max_attempts=3)

    executed = JobRunner(database).run_due(datetime(2026, 8, 14, 9, 0, tzinfo=UTC))

    assert len(executed) == 1
    assert executed[0].outbox_id is not None
    with database.connect() as connection:
        job = connection.execute("SELECT state, next_run_at, payload_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
        message = json.loads(
            connection.execute("SELECT payload_json FROM outbox WHERE job_id = ?", (job_id,)).fetchone()[0]
        )
    assert job["state"] == "active"
    assert datetime.fromisoformat(job["next_run_at"]) == datetime(2026, 8, 14, 10, 0, tzinfo=UTC)
    assert json.loads(job["payload_json"])["attempt"] == 2
    assert message["text"] == "Reminder: Submit the paper"


def test_nag_silences_when_task_is_completed(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    job_id, task_id = _create_nag(database)

    with database.connect() as connection:
        with database.transaction(connection):
            TaskStore.complete(connection, task_id)

    executed = JobRunner(database).run_due(datetime(2026, 8, 14, 9, 0, tzinfo=UTC))

    assert len(executed) == 1
    assert executed[0].outbox_id is None
    with database.connect() as connection:
        job = connection.execute("SELECT state, next_run_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
        outbox_count = connection.execute("SELECT COUNT(*) FROM outbox WHERE job_id = ?", (job_id,)).fetchone()[0]
    assert job["state"] == "completed"
    assert job["next_run_at"] is None
    assert outbox_count == 0


def test_nag_delivers_final_message_on_last_attempt(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    job_id, _task_id = _create_nag(database, max_attempts=2, interval_hours=1.0)

    JobRunner(database).run_due(datetime(2026, 8, 14, 9, 0, tzinfo=UTC))
    executed = JobRunner(database).run_due(datetime(2026, 8, 14, 10, 0, tzinfo=UTC))

    assert len(executed) == 1
    with database.connect() as connection:
        job = connection.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
        messages = [
            json.loads(row[0])["text"]
            for row in connection.execute(
                "SELECT payload_json FROM outbox WHERE job_id = ? ORDER BY created_at ASC",
                (job_id,),
            ).fetchall()
        ]
    assert job["state"] == "completed"
    assert messages == ["Reminder: Submit the paper", "Last reminder (2 of 2): Submit the paper"]


def test_nag_store_rejects_invalid_interval_or_attempts(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection,
                source="test",
                external_id="bad",
                occurred_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
                content="x",
                metadata={},
            )
            task = TaskStore.create(connection, title="x", source_event_id=event.id)
            with pytest.raises(ValueError, match="interval_hours"):
                NagStore.create(
                    connection,
                    run_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
                    task_id=task.id,
                    destination="telegram:20",
                    text="x",
                    interval_hours=0,
                    max_attempts=3,
                    idempotency_key="bad-interval",
                )
            with pytest.raises(ValueError, match="max_attempts"):
                NagStore.create(
                    connection,
                    run_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
                    task_id=task.id,
                    destination="telegram:20",
                    text="x",
                    interval_hours=1,
                    max_attempts=0,
                    idempotency_key="bad-attempts",
                )


@pytest.mark.parametrize(
    "phrase",
    (
        "keep reminding me until I finish the essay",
        "nag me about laundry every few hours",
        "remind me until done to call mom",
        "keep reminding me to stretch every morning",
    ),
)
def test_nag_phrases_select_nag_until_done_tool(phrase: str) -> None:
    tools = select_hermes_tools(phrase)
    assert "nag_until_done" in tools
