from datetime import UTC, datetime
from pathlib import Path

import pytest

from alfred.db import Database
from alfred.jobs import JobRunner
from alfred.outbox import Outbox
from alfred.slack import SlackEvent, SlackGateway, SlackOutboxWorker, SlackPair


class FakeSlack:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def post_message(self, *, channel_id: str, text: str) -> str:
        self.sent.append((channel_id, text))
        return "123.456"


def _gateway(path: Path) -> SlackGateway:
    return SlackGateway(Database(path), {SlackPair(channel_id="D123", user_id="U123")})


def _event(text: str = "/task read notes") -> SlackEvent:
    return SlackEvent(type="message", user="U123", channel="D123", text=text, ts="1786198400.000001")


def test_paired_slack_message_creates_a_task_and_one_receipt(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path / "alfred.db")

    first = gateway.handle_event(event_id="Ev1", event=_event())
    second = gateway.handle_event(event_id="Ev1", event=_event())

    assert first.task_id is not None
    assert second.duplicate is True
    with Database(tmp_path / "alfred.db").connect() as connection:
        task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        destination = connection.execute("SELECT destination FROM outbox").fetchone()[0]
    assert task_count == 1
    assert destination == "slack:D123"


def test_unpaired_slack_sender_is_rejected_without_persisting(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path / "alfred.db")

    with pytest.raises(PermissionError):
        gateway.handle_event(event_id="Ev1", event=SlackEvent(type="message", user="U999", channel="D123", text="/task no", ts="1"))

    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_slack_reminder_routes_back_to_the_paired_channel(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    receipt = _gateway(database_path).handle_event(
        event_id="Ev2",
        event=_event("/remind 2026-08-14T09:00:00Z submit paper"),
    )

    JobRunner(Database(database_path)).run_due(datetime(2026, 8, 14, 9, 0, tzinfo=UTC))
    transport = FakeSlack()
    result = SlackOutboxWorker(Database(database_path), transport, {"D123"}).deliver_pending()

    assert receipt.reminder_job_id is not None
    assert [item[0] for item in transport.sent] == ["D123", "D123"]
    assert len(result) == 2


def test_bubbles_enqueued_in_one_second_deliver_in_order(tmp_path: Path) -> None:
    """Regression: this path tie-broke on `id`, a random uuid4, while
    created_at only has second granularity. A four-part agent answer
    therefore shipped scrambled -- observed on all six trial runs -- the
    same defect Telegram already fixed by ordering on rowid."""
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    expected = ["first", "second", "third", "fourth"]
    with database.connect() as connection:
        with database.transaction(connection):
            for index, text in enumerate(expected):
                Outbox.enqueue(
                    connection,
                    destination="slack:D123",
                    payload={"text": text},
                    idempotency_key=f"hermes-reply:99:{index}",
                )
    transport = FakeSlack()

    SlackOutboxWorker(database, transport, {"D123"}).deliver_pending()

    assert [text for _, text in transport.sent] == expected


def test_slack_outbox_fails_closed_for_an_unpaired_channel(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                "INSERT INTO outbox (id, destination, payload_json, idempotency_key, state) VALUES ('one', 'slack:D999', '{\"text\":\"no\"}', 'one', 'pending')"
            )

    result = SlackOutboxWorker(database, FakeSlack(), {"D123"}).deliver_pending()

    assert result[0]["state"] == "failed"
