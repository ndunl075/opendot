from pathlib import Path

from alfred.db import Database
from alfred.gmail_inbound import GmailInboundGateway


def _message(message_id: str, internal_date: str, *, subject: str, sender: str = "nico@example.com") -> dict:
    return {
        "id": message_id,
        "internalDate": internal_date,
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": f"Nico <{sender}>"},
            ]
        },
    }


class FakeInbox:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = messages

    def list_unread_inbox(self, *, limit=500):
        return self.messages


def test_task_command_from_an_allowed_sender_creates_a_task(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    gateway = GmailInboundGateway(
        database, FakeInbox([_message("1", "1786190400000", subject="Task: Buy milk")]), {"nico@example.com"}
    )

    result = gateway.poll()

    assert (result.received, result.handled, result.duplicate, result.rejected, result.ignored) == (1, 1, 0, 0, 0)
    with database.connect() as connection:
        row = connection.execute("SELECT title, state, due_at FROM tasks").fetchone()
    assert (row["title"], row["state"], row["due_at"]) == ("Buy milk", "open", None)


def test_polling_twice_does_not_duplicate_the_task(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    messages = [_message("1", "1786190400000", subject="Task: Buy milk")]

    first = GmailInboundGateway(database, FakeInbox(messages), {"nico@example.com"}).poll()
    second = GmailInboundGateway(database, FakeInbox(messages), {"nico@example.com"}).poll()

    assert (first.handled, first.duplicate) == (1, 0)
    assert (second.handled, second.duplicate) == (0, 1)
    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert count == 1


def test_remind_command_schedules_a_reminder_when_a_destination_is_configured(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    gateway = GmailInboundGateway(
        database,
        FakeInbox([_message("1", "1786190400000", subject="Remind: 2026-08-20T09:00:00Z Renew passport")]),
        {"nico@example.com"},
        default_reminder_destination="telegram:123",
    )

    result = gateway.poll()

    assert result.handled == 1
    with database.connect() as connection:
        task = connection.execute("SELECT title, due_at FROM tasks").fetchone()
        job = connection.execute("SELECT kind, payload_json FROM jobs").fetchone()
    assert task["title"] == "Renew passport"
    assert task["due_at"] == "2026-08-20T09:00:00+00:00"
    assert job["kind"] == "reminder"
    assert "telegram:123" in job["payload_json"]


def test_remind_command_without_a_configured_destination_only_creates_the_task(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    gateway = GmailInboundGateway(
        database,
        FakeInbox([_message("1", "1786190400000", subject="Remind: 2026-08-20T09:00:00Z Renew passport")]),
        {"nico@example.com"},
    )

    result = gateway.poll()

    assert result.handled == 1
    with database.connect() as connection:
        task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        job_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert (task_count, job_count) == (1, 0)


def test_command_from_an_unallowed_sender_is_rejected_not_executed(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    gateway = GmailInboundGateway(
        database,
        FakeInbox([_message("1", "1786190400000", subject="Task: Buy milk", sender="stranger@example.com")]),
        {"nico@example.com"},
    )

    result = gateway.poll()

    assert (result.handled, result.rejected) == (0, 1)
    with database.connect() as connection:
        task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        audited = connection.execute(
            "SELECT outcome FROM tool_runs WHERE tool = 'gmail_inbound_command'"
        ).fetchone()
    assert task_count == 0
    assert audited["outcome"] == "rejected"


def test_ordinary_mail_without_a_recognized_subject_is_silently_ignored(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    gateway = GmailInboundGateway(
        database, FakeInbox([_message("1", "1786190400000", subject="Re: capstone review")]), {"nico@example.com"}
    )

    result = gateway.poll()

    assert (result.handled, result.rejected, result.ignored) == (0, 0, 1)
    with database.connect() as connection:
        task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        audit_count = connection.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0]
    assert (task_count, audit_count) == (0, 0)


def test_malformed_remind_time_is_ignored_rather_than_raising(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    gateway = GmailInboundGateway(
        database,
        FakeInbox([_message("1", "1786190400000", subject="Remind: not-a-time Renew passport")]),
        {"nico@example.com"},
    )

    result = gateway.poll()

    assert result.ignored == 1
    with database.connect() as connection:
        task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert task_count == 0


def test_allowed_senders_are_matched_case_insensitively(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    gateway = GmailInboundGateway(
        database,
        FakeInbox([_message("1", "1786190400000", subject="Task: Buy milk", sender="Nico@Example.com")]),
        {"nico@example.com"},
    )

    result = gateway.poll()

    assert result.handled == 1
