from pathlib import Path
import json

import httpx

from alfred.db import Database
from alfred.outbox import Outbox
from alfred.telegram import TelegramPair
from alfred.telegram_bot import TelegramBotClient
from alfred.telegram_runtime import TelegramLongPoller, TelegramOutboxWorker


class FakeTelegram:
    def __init__(self, updates: list[dict] | None = None) -> None:
        self.updates = updates or []
        self.offsets: list[int | None] = []
        self.sent: list[tuple[int, str]] = []

    def get_updates(self, *, offset: int | None, timeout_seconds: int = 25) -> list[dict]:
        self.offsets.append(offset)
        return [update for update in self.updates if offset is None or update.get("update_id", -1) >= offset]

    def send_message(self, *, chat_id: int, text: str) -> int:
        self.sent.append((chat_id, text))
        return 123


def test_long_poller_persists_cursor_and_uses_idempotent_gateway(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    fake = FakeTelegram(
        [
            {
                "update_id": 41,
                "message": {
                    "message_id": 1,
                    "date": 1_786_198_400,
                    "chat": {"id": 20},
                    "from": {"id": 10},
                    "text": "/task read notes",
                },
            }
        ]
    )
    poller = TelegramLongPoller(database, fake, {TelegramPair(chat_id=20, user_id=10)})

    first = poller.poll_once(timeout_seconds=1)
    second = poller.poll_once(timeout_seconds=1)

    assert first.model_dump() == {"received": 1, "handled": 1, "rejected": 0, "cursor": 41}
    assert second.cursor == 41
    assert fake.offsets == [None, 42]
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert connection.execute("SELECT cursor FROM sync_state WHERE connector = 'telegram'").fetchone()[0] == "41"


def test_outbox_worker_sends_only_allowed_chat_once(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            message = Outbox.enqueue(
                connection,
                destination="telegram:20",
                payload={"text": "hello"},
                idempotency_key="test-message",
            )
    fake = FakeTelegram()
    worker = TelegramOutboxWorker(database, fake, {20})

    first = worker.deliver_pending()
    second = worker.deliver_pending()

    assert first[0].state == "sent"
    assert second == []
    assert fake.sent == [(20, "hello")]
    with database.connect() as connection:
        assert connection.execute("SELECT state FROM outbox WHERE id = ?", (message.id,)).fetchone()[0] == "sent"


def test_outbox_worker_fails_closed_for_unpaired_destination(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            message = Outbox.enqueue(
                connection,
                destination="telegram:99",
                payload={"text": "do not send"},
                idempotency_key="unpaired-message",
            )
    fake = FakeTelegram()

    result = TelegramOutboxWorker(database, fake, {20}).deliver_pending()

    assert result[0].state == "failed"
    assert fake.sent == []
    with database.connect() as connection:
        row = connection.execute("SELECT state, last_error FROM outbox WHERE id = ?", (message.id,)).fetchone()
    assert row["state"] == "failed"
    assert "not a locally allowed" in row["last_error"]


def test_bot_client_uses_https_api_contract_without_exposing_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/botTOKEN/sendMessage"
        assert json.loads(request.content) == {"chat_id": 20, "text": "hello"}
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    client = TelegramBotClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        assert client.send_message(chat_id=20, text="hello") == 7
    finally:
        client.close()
