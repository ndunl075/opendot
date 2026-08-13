import json
from datetime import UTC, datetime
from pathlib import Path

from alfred.briefing import BriefingService
from alfred.db import Database
from alfred.jobs import JobRunner
from alfred.memory_graph import MemoryGraph
from alfred.memory_learning import MemoryLearningService
from alfred.telegram import TelegramGateway, TelegramPair, TelegramUpdate


def test_natural_deadline_survives_restart_and_reaches_reminder_and_brief(tmp_path: Path) -> None:
    """Architecture section 10's first acceptance path, without wall-clock waiting."""
    database_path = tmp_path / "alfred.db"
    received_at = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)  # Thursday morning in New York
    update = TelegramUpdate.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": int(received_at.timestamp()),
                "chat": {"id": 20},
                "from": {"id": 10},
                "text": "my paper is due Friday; remind me Thursday",
            },
        }
    )
    database = Database(database_path)

    receipt = TelegramGateway(
        database,
        {TelegramPair(chat_id=20, user_id=10)},
        defer_unparsed_to_agent=True,
    ).handle(update)
    learned = MemoryLearningService(database).run_once()

    assert receipt.task_id and receipt.reminder_job_id
    assert learned.promoted == 1
    recalled = MemoryGraph(database).search("paper Friday")
    assert recalled.memories[0].source_event_id

    # Reopen the database as a new process would after restart, advance to the
    # scheduled reminder, and prove both scheduler and next brief see it.
    restarted = Database(database_path)
    with restarted.connect() as connection:
        reminder_at = datetime.fromisoformat(
            connection.execute("SELECT next_run_at FROM jobs WHERE id = ?", (receipt.reminder_job_id,)).fetchone()[0]
        )
        due_at = datetime.fromisoformat(
            connection.execute("SELECT due_at FROM tasks WHERE id = ?", (receipt.task_id,)).fetchone()[0]
        )
    executed = JobRunner(restarted).run_due(reminder_at)
    brief = BriefingService(restarted).morning_brief(
        due_at.replace(hour=9, minute=0), timezone_name="America/New_York"
    )

    assert executed[0].id == receipt.reminder_job_id
    with restarted.connect() as connection:
        messages = [json.loads(row[0])["text"] for row in connection.execute("SELECT payload_json FROM outbox")]
    assert any(message.startswith("Reminder: paper") for message in messages)
    assert [item.title for item in brief.due_today] == ["paper"]
