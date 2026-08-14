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
        self.sent: list[tuple[int, str, dict | None]] = []
        self.chat_actions: list[tuple[int, str]] = []
        self.reactions: list[tuple[int, int, str]] = []
        self.callback_answers: list[tuple[str, str]] = []

    def get_updates(self, *, offset: int | None, timeout_seconds: int = 25) -> list[dict]:
        self.offsets.append(offset)
        return [update for update in self.updates if offset is None or update.get("update_id", -1) >= offset]

    def send_message(self, *, chat_id: int, text: str, reply_markup: dict | None = None) -> int:
        self.sent.append((chat_id, text, reply_markup))
        return 123

    def send_chat_action(self, *, chat_id: int, action: str = "typing") -> None:
        self.chat_actions.append((chat_id, action))

    def set_message_reaction(self, *, chat_id: int, message_id: int, emoji: str) -> None:
        self.reactions.append((chat_id, message_id, emoji))

    def answer_callback_query(self, *, callback_query_id: str, text: str) -> None:
        self.callback_answers.append((callback_query_id, text))


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


def test_typing_indicator_failure_never_loses_a_deferred_message(tmp_path: Path) -> None:
    class BrokenTypingTelegram(FakeTelegram):
        def send_chat_action(self, *, chat_id: int, action: str = "typing") -> None:
            raise TimeoutError("cosmetic request failed")

    fake = BrokenTypingTelegram(
        [
            {
                "update_id": 42,
                "message": {
                    "message_id": 2,
                    "date": 1_786_198_400,
                    "chat": {"id": 20},
                    "from": {"id": 10},
                    "text": "yo",
                },
            }
        ]
    )

    result = TelegramLongPoller(
        Database(tmp_path / "alfred.db"),
        fake,
        {TelegramPair(chat_id=20, user_id=10)},
        defer_unparsed_to_agent=True,
    ).poll_once(timeout_seconds=1)

    assert result.handled == 1
    with Database(tmp_path / "alfred.db").connect() as connection:
        row = connection.execute(
            "SELECT metadata_json FROM events WHERE source = 'telegram' AND external_id = '42'"
        ).fetchone()
    assert json.loads(row["metadata_json"])["agent_deferred"] is True


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
    assert fake.sent == [(20, "hello", None)]
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


def test_bot_client_bounds_long_poll_read_timeout_close_to_server_wait() -> None:
    observed: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"ok": True, "result": []})

    client = TelegramBotClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        assert client.get_updates(offset=None, timeout_seconds=10) == []
    finally:
        client.close()

    assert observed == [12.0]


def test_bot_client_sends_feedback_keyboard_and_answers_callback() -> None:
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append((request.url.path, payload))
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
        return httpx.Response(200, json={"ok": True, "result": True})

    markup = {"inline_keyboard": [[{"text": "helpful", "callback_data": "af:40:h"}]]}
    client = TelegramBotClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        assert client.send_message(chat_id=20, text="answer", reply_markup=markup) == 7
        client.answer_callback_query(callback_query_id="callback-1", text="thanks")
    finally:
        client.close()

    assert calls == [
        (
            "/botTOKEN/sendMessage",
            {"chat_id": 20, "text": "answer", "reply_markup": markup},
        ),
        (
            "/botTOKEN/answerCallbackQuery",
            {"callback_query_id": "callback-1", "text": "thanks"},
        ),
    ]


def test_bot_client_sends_a_short_bounded_typing_action() -> None:
    calls: list[tuple[str, dict, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (
                request.url.path,
                json.loads(request.content),
                request.extensions["timeout"]["read"],
            )
        )
        return httpx.Response(200, json={"ok": True, "result": True})

    client = TelegramBotClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        client.send_chat_action(chat_id=20)
    finally:
        client.close()

    assert calls == [("/botTOKEN/sendChatAction", {"chat_id": 20, "action": "typing"}, 2.0)]


def test_bubbles_enqueued_in_one_second_deliver_in_order(tmp_path: Path) -> None:
    """Regression: the outbox tie-broke on `id`, a random uuid4, and
    created_at only has second granularity. A four-part agent answer
    therefore shipped scrambled, with its closing question arriving before
    the details it was asking about."""
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            for index, text in enumerate(["first", "second", "third", "fourth"]):
                Outbox.enqueue(
                    connection,
                    destination="telegram:20",
                    payload={"text": text},
                    idempotency_key=f"hermes-reply:99:{index}",
                )
    fake = FakeTelegram()

    TelegramOutboxWorker(database, fake, {20}).deliver_pending()

    assert [text for _, text, _ in fake.sent] == ["first", "second", "third", "fourth"]


def test_feedback_callback_is_paired_content_free_and_one_vote_per_response(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from alfred.response_feedback import ResponseFeedbackService
    from alfred.telegram import TelegramGateway, TelegramUpdate

    database = Database(tmp_path / "alfred.db")
    pair = TelegramPair(chat_id=20, user_id=10)
    TelegramGateway(database, {pair}, defer_unparsed_to_agent=True).handle(
        TelegramUpdate.model_validate(
            {
                "update_id": 40,
                "message": {
                    "message_id": 140,
                    "date": int(datetime.now(UTC).timestamp()),
                    "chat": {"id": 20},
                    "from": {"id": 10},
                    "text": "what matters in my inbox?",
                },
            }
        )
    )
    with database.connect() as connection:
        with database.transaction(connection):
            ResponseFeedbackService.record_context_in_transaction(
                connection,
                response_update_id="40",
                sources=["gmail"],
                freshness={"gmail": "2026-08-14T03:42:23Z"},
                items=[{"source": "gmail", "record_id": "message-1", "rank": 0}],
            )

    fake = FakeTelegram(
        [
            {
                "update_id": 41,
                "callback_query": {
                    "id": "callback-1",
                    "from": {"id": 10},
                    "message": {"message_id": 240, "chat": {"id": 20}},
                    "data": "af:40:h",
                },
            },
            {
                "update_id": 42,
                "callback_query": {
                    "id": "callback-2",
                    "from": {"id": 10},
                    "message": {"message_id": 240, "chat": {"id": 20}},
                    "data": "af:40:w",
                },
            },
        ]
    )

    result = TelegramLongPoller(database, fake, {pair}).poll_once(timeout_seconds=1)

    assert (result.received, result.handled, result.rejected) == (2, 2, 0)
    assert fake.callback_answers == [
        ("callback-1", "thanks, that helps"),
        ("callback-2", "feedback already saved"),
    ]
    with database.connect() as connection:
        feedback = connection.execute(
            "SELECT outcome, response_update_id FROM response_feedback"
        ).fetchall()
        approval_count = connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
        callback_events = connection.execute(
            "SELECT content, metadata_json FROM events WHERE external_id IN ('41', '42') ORDER BY external_id"
        ).fetchall()
    assert [(row["outcome"], row["response_update_id"]) for row in feedback] == [("helpful", "40")]
    assert approval_count == 0
    assert [row["content"] for row in callback_events] == [
        "response feedback",
        "response feedback",
    ]
    assert "what matters" not in "".join(row["metadata_json"] for row in callback_events)
