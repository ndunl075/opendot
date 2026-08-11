from datetime import datetime
from pathlib import Path

import pytest

from alfred.db import Database
from alfred.telegram import TelegramGateway, TelegramPair, TelegramUpdate


def _update(update_id: int, text: str, *, chat_id: int = 20, user_id: int = 10) -> TelegramUpdate:
    return TelegramUpdate.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id + 100,
                "date": 1_786_198_400,
                "chat": {"id": chat_id},
                "from": {"id": user_id},
                "text": text,
            },
        }
    )


def _gateway(path: Path) -> TelegramGateway:
    return TelegramGateway(Database(path), {TelegramPair(chat_id=20, user_id=10)})


def test_task_update_creates_event_task_and_receipt_once(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    gateway = _gateway(database_path)

    first = gateway.handle(_update(1, "/task submit paper"))
    second = gateway.handle(_update(1, "/task submit paper"))

    assert first.text == "Saved task: submit paper"
    assert first.task_id is not None
    assert second.text == first.text
    assert second.duplicate is True
    with Database(database_path).connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1


def test_reminder_update_creates_task_and_scheduled_job(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    receipt = _gateway(database_path).handle(_update(2, "/remind 2026-08-14T09:00:00Z submit paper"))

    assert receipt.task_id is not None
    assert receipt.reminder_job_id is not None
    with Database(database_path).connect() as connection:
        task = connection.execute("SELECT due_at FROM tasks WHERE id = ?", (receipt.task_id,)).fetchone()
        job = connection.execute("SELECT next_run_at, state FROM jobs WHERE id = ?", (receipt.reminder_job_id,)).fetchone()

    assert datetime.fromisoformat(task["due_at"]).isoformat() == "2026-08-14T09:00:00+00:00"
    assert datetime.fromisoformat(job["next_run_at"]).isoformat() == "2026-08-14T09:00:00+00:00"
    assert job["state"] == "active"


def test_unpaired_telegram_identity_cannot_create_records(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    gateway = _gateway(database_path)

    with pytest.raises(PermissionError, match="not locally paired"):
        gateway.handle(_update(3, "/task should not exist", user_id=999))

    database = Database(database_path)
    database.migrate()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_bad_command_gets_a_help_receipt_without_a_task(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    receipt = _gateway(database_path).handle(_update(4, "remember this forever"))

    assert "Use /task" in receipt.text
    with Database(database_path).connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
