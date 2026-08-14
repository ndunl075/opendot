import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alfred.db import Database
from alfred.hermes_bridge import AgentRunResult, HermesBridge
from alfred.policy import ApprovalService
from alfred.response_feedback import ResponseFeedbackService
from alfred.telegram import TelegramGateway, TelegramPair, TelegramUpdate
from alfred.telegram_actions import TelegramActionWorker


class FakeSecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_optional(self, name: str) -> str | None:
        return self.values.get(name)

    def store(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


def _message(update_id: int, text: str = "send it") -> TelegramUpdate:
    return TelegramUpdate.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id + 100,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": 20},
                "from": {"id": 10},
                "text": text,
            },
        }
    )


def _callback(update_id: int, approval_id: str, code: str, *, user_id: int = 10) -> TelegramUpdate:
    return TelegramUpdate.model_validate(
        {
            "update_id": update_id,
            "callback_query": {
                "id": f"callback-{update_id}",
                "from": {"id": user_id},
                "message": {"message_id": 500, "chat": {"id": 20}},
                "data": f"aa:{approval_id}:{code}",
            },
        }
    )


def _linked_approval(database: Database) -> str:
    gateway = TelegramGateway(database, {TelegramPair(chat_id=20, user_id=10)}, defer_unparsed_to_agent=True)
    gateway.handle(_message(1))
    approval = ApprovalService(database).propose(
        actor="mcp:hermes",
        action_type="gmail_message_send",
        preview={"to": "person@example.com", "subject": "hello", "body": "hi"},
    )
    with database.connect() as connection:
        with database.transaction(connection):
            ResponseFeedbackService.record_context_in_transaction(
                connection,
                response_update_id="1",
                sources=[],
                freshness={},
                items=[],
            )
            connection.execute(
                """
                INSERT INTO telegram_action_links (
                    approval_id, response_update_id, chat_id, user_id, action_type, created_at
                ) VALUES (?, '1', 20, 10, 'gmail_message_send', ?)
                """,
                (approval.id, datetime.now(UTC).isoformat()),
            )
    return approval.id


def test_bridge_attaches_a_new_proposal_to_the_answer_keyboard(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    gateway = TelegramGateway(database, {TelegramPair(chat_id=20, user_id=10)}, defer_unparsed_to_agent=True)
    gateway.handle(_message(11, "draft an email to person@example.com"))

    def proposing_agent(prompt: str) -> AgentRunResult:
        ApprovalService(database).propose(
            actor="mcp:hermes",
            action_type="gmail_draft_create",
            preview={"to": "person@example.com", "subject": "hello", "body": "hi"},
        )
        return AgentRunResult(text="draft ready for approval.", ok=True)

    HermesBridge(database, proposing_agent).run_once()

    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM outbox WHERE idempotency_key = 'hermes-reply:11:0'"
        ).fetchone()
        link_count = connection.execute("SELECT COUNT(*) FROM telegram_action_links").fetchone()[0]
    payload = json.loads(row["payload_json"])
    first_row = payload["reply_markup"]["inline_keyboard"][0]
    assert first_row[0]["text"] == "approve email draft"
    assert first_row[0]["callback_data"].startswith("aa:")
    assert link_count == 1


def test_telegram_approval_executes_once_without_persisting_the_raw_token(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    approval_id = _linked_approval(database)
    pair = TelegramPair(chat_id=20, user_id=10)
    receipt = TelegramGateway(database, {pair}).handle(_callback(2, approval_id, "y"))
    captured: list[str] = []

    def execute(target: str, actor: str, token: str) -> dict:
        captured.append(token)
        ApprovalService(database).consume(target, actor=actor, token=token)
        return {"sent": True}

    secrets = FakeSecrets()
    worker = TelegramActionWorker(database, executor=execute, secret_store=secrets)

    assert receipt.text == "approved. doing it now"
    assert worker.run_pending() == 1
    assert worker.run_pending() == 0
    assert len(captured) == 1
    assert secrets.values == {}
    with database.connect() as connection:
        approval = connection.execute("SELECT state, token_hash FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        intent = connection.execute("SELECT state, attempts FROM telegram_action_intents").fetchone()
        outbox = connection.execute(
            "SELECT payload_json FROM outbox WHERE idempotency_key = ?",
            (f"telegram-action-result:{approval_id}",),
        ).fetchone()
    assert approval["state"] == "consumed"
    assert approval["token_hash"] != captured[0]
    assert intent["state"] == "completed"
    assert json.loads(outbox["payload_json"])["text"] == "sent."


def test_cancel_rejects_without_calling_the_executor(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    approval_id = _linked_approval(database)
    pair = TelegramPair(chat_id=20, user_id=10)
    TelegramGateway(database, {pair}).handle(_callback(2, approval_id, "n"))

    def must_not_execute(target: str, actor: str, token: str) -> dict:
        raise AssertionError("cancel must not execute")

    assert TelegramActionWorker(
        database, executor=must_not_execute, secret_store=FakeSecrets()
    ).run_pending() == 1
    assert ApprovalService(database).get(approval_id).state == "rejected"


def test_action_button_rejects_a_different_sender(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    approval_id = _linked_approval(database)
    gateway = TelegramGateway(
        database,
        {TelegramPair(chat_id=20, user_id=10), TelegramPair(chat_id=20, user_id=99)},
    )
    with pytest.raises(PermissionError):
        gateway.handle(_callback(2, approval_id, "y", user_id=99))
    assert ApprovalService(database).get(approval_id).state == "pending"
