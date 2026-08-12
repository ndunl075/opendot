from datetime import UTC, datetime
from pathlib import Path

from alfred.audit import AuditLog
from alfred.db import Database
from alfred.events import EventStore
from alfred.jobs import JobRunner
from alfred.reminders import ReminderStore
from alfred.tasks import TaskStore
from alfred.telegram import TelegramGateway, TelegramPair, TelegramUpdate


def _reminder_gateway(path: Path) -> TelegramGateway:
    return TelegramGateway(Database(path), {TelegramPair(chat_id=20, user_id=10)})


def _reminder_update() -> TelegramUpdate:
    return TelegramUpdate.model_validate(
        {
            "update_id": 7,
            "message": {
                "message_id": 1,
                "date": 1_786_198_400,
                "chat": {"id": 20},
                "from": {"id": 10},
                "text": "/remind 2026-08-14T09:00:00Z submit paper",
            },
        }
    )


def test_due_job_moves_to_outbox_once_and_is_audited(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    receipt = _reminder_gateway(database_path).handle(_reminder_update())

    first = JobRunner(Database(database_path)).run_due(datetime(2026, 8, 14, 9, 0, tzinfo=UTC))
    second = JobRunner(Database(database_path)).run_due(datetime(2026, 8, 14, 9, 1, tzinfo=UTC))

    assert len(first) == 1
    assert first[0].id == receipt.reminder_job_id
    assert first[0].late is False
    assert second == []
    with Database(database_path).connect() as connection:
        assert connection.execute("SELECT state FROM jobs WHERE id = ?", (receipt.reminder_job_id,)).fetchone()[0] == "completed"
        assert connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 2
        delivery = connection.execute("SELECT payload_json FROM outbox WHERE job_id = ?", (receipt.reminder_job_id,)).fetchone()
    assert "Reminder: submit paper" in delivery["payload_json"]
    assert AuditLog(Database(database_path)).verify() is True


def test_late_reminder_is_labeled(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    receipt = _reminder_gateway(database_path).handle(_reminder_update())

    executed = JobRunner(Database(database_path)).run_due(datetime(2026, 8, 14, 10, 0, tzinfo=UTC))

    assert executed[0].late is True
    with Database(database_path).connect() as connection:
        delivery = connection.execute("SELECT payload_json FROM outbox WHERE job_id = ?", (receipt.reminder_job_id,)).fetchone()
    assert "Late reminder (scheduled 2026-08-14T09:00:00+00:00): submit paper" in delivery["payload_json"]


def test_reminder_keeps_an_explicit_non_telegram_destination(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection,
                source="slack",
                external_id="event-1",
                occurred_at=datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
                content="Check Slack",
                metadata={},
            )
            task = TaskStore.create(connection, title="Check Slack", source_event_id=event.id)
            job = ReminderStore.create(
                connection,
                run_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
                task_id=task.id,
                destination="slack:D123",
                text="Check Slack",
                idempotency_key="slack-reminder",
            )

    JobRunner(database).run_due(datetime(2026, 8, 14, 9, 0, tzinfo=UTC))

    with database.connect() as connection:
        destination = connection.execute("SELECT destination FROM outbox WHERE job_id = ?", (job.id,)).fetchone()[0]
    assert destination == "slack:D123"
