"""Telegram update translation without a network dependency or delivery side effect."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .db import Database
from .events import EventStore
from .outbox import Outbox
from .reminders import ReminderStore
from .tasks import TaskStore


class TelegramChat(BaseModel):
    id: int


class TelegramUser(BaseModel):
    id: int


class TelegramMessage(BaseModel):
    message_id: int
    date: int
    chat: TelegramChat
    sender: TelegramUser = Field(alias="from")
    text: str | None = None


class TelegramUpdate(BaseModel):
    update_id: int
    message: TelegramMessage | None = None


class TelegramReceipt(BaseModel):
    text: str
    task_id: str | None = None
    reminder_job_id: str | None = None
    duplicate: bool = False
    ignored: bool = False
    agent_deferred: bool = False


@dataclass(frozen=True)
class TelegramPair:
    chat_id: int
    user_id: int


class TelegramGateway:
    """Accept updates from locally paired identities and create durable intents."""

    #: Receipt sent immediately when a message is handed to the agent, and
    #: delivered before the agent runs so it lands while the answer is still
    #: being written. Lowercase and terse to match the persona in SOUL.md.
    agent_ack_text = "one sec"

    def __init__(
        self,
        database: Database,
        allowed_pairs: set[TelegramPair],
        *,
        defer_unparsed_to_agent: bool = False,
    ) -> None:
        self.database = database
        self.allowed_pairs = allowed_pairs
        self.defer_unparsed_to_agent = defer_unparsed_to_agent

    @classmethod
    def from_path(
        cls,
        database_path: Path | str,
        allowed_pairs: set[TelegramPair],
        *,
        defer_unparsed_to_agent: bool = False,
    ) -> "TelegramGateway":
        return cls(Database(database_path), allowed_pairs, defer_unparsed_to_agent=defer_unparsed_to_agent)

    def handle(self, update: TelegramUpdate) -> TelegramReceipt:
        """Translate one update atomically; no network message is sent here."""
        message = update.message
        if message is None or not message.text:
            return TelegramReceipt(text="Ignored non-text Telegram update.", ignored=True)
        text = message.text
        pair = TelegramPair(chat_id=message.chat.id, user_id=message.sender.id)
        if pair not in self.allowed_pairs:
            raise PermissionError("Telegram sender is not locally paired with Alfred")

        # Parsed once here rather than inside the transaction: the metadata
        # marker below has to be written by the same INSERT that stores the
        # event, and `hermes_bridge` later reads that marker instead of
        # re-deriving the decision with its own copy of this parser.
        try:
            parsed: tuple[str, str, datetime | None] | None = self._parse_command(text)
            parse_error: str | None = None
        except ValueError as error:
            parsed, parse_error = None, str(error)
        deferred = parsed is None and self.defer_unparsed_to_agent

        self.database.migrate()
        event_time = datetime.fromtimestamp(message.date, UTC)
        metadata: dict[str, Any] = {
            "chat_id": message.chat.id,
            "message_id": message.message_id,
            "user_id": message.sender.id,
        }
        if deferred:
            metadata["agent_deferred"] = True
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                stored_event = EventStore.append(
                    connection,
                    source="telegram",
                    external_id=str(update.update_id),
                    occurred_at=event_time,
                    content=text,
                    metadata=metadata,
                )
                receipt_key = f"telegram-receipt:{update.update_id}"
                if not stored_event.is_new:
                    return self._existing_receipt(connection, receipt_key)
                receipt = self._handle_new_event(
                    connection,
                    stored_event.id,
                    parsed=parsed,
                    parse_error=parse_error,
                    deferred=deferred,
                    chat_id=message.chat.id,
                    update_id=update.update_id,
                )
                Outbox.enqueue(
                    connection,
                    destination=f"telegram:{message.chat.id}",
                    payload={"text": receipt.text},
                    idempotency_key=receipt_key,
                )
                return receipt

    def _handle_new_event(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        *,
        parsed: tuple[str, str, datetime | None] | None,
        parse_error: str | None,
        deferred: bool,
        chat_id: int,
        update_id: int,
    ) -> TelegramReceipt:
        if parsed is None:
            if deferred:
                # Deliberately only an acknowledgement: the agent turn takes
                # seconds and must not run inside this write transaction, so
                # `hermes_bridge` sends the real answer as a second message.
                return TelegramReceipt(text=self.agent_ack_text, agent_deferred=True)
            return TelegramReceipt(text=f"{parse_error} Use /task <title> or /remind <ISO-8601 time> <title>.")

        command, title, run_at = parsed
        task = TaskStore.create(connection, title=title, source_event_id=event_id, due_at=run_at)
        if command == "task":
            return TelegramReceipt(text=f"Saved task: {task.title}", task_id=task.id)
        reminder = ReminderStore.create(
            connection,
                run_at=run_at,
                task_id=task.id,
                destination=f"telegram:{chat_id}",
            text=task.title,
            idempotency_key=f"telegram-reminder:{update_id}",
        )
        return TelegramReceipt(
            text=f"Saved reminder for {reminder.run_at.isoformat()}: {task.title}",
            task_id=task.id,
            reminder_job_id=reminder.id,
        )

    @staticmethod
    def _parse_command(text: str) -> tuple[str, str, datetime | None]:
        normalized = text.strip()
        if normalized.startswith("/task"):
            title = normalized.removeprefix("/task").strip()
            if title:
                return "task", title, None
            raise ValueError("Task title is required.")
        if normalized.startswith("/remind"):
            parts = normalized.split(maxsplit=2)
            if len(parts) != 3:
                raise ValueError("Reminder needs a time and title.")
            try:
                run_at = datetime.fromisoformat(parts[1].replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("Reminder time must be ISO-8601, for example 2026-08-14T09:00:00Z.") from error
            if run_at.tzinfo is None:
                raise ValueError("Reminder time must include a timezone.")
            if not parts[2].strip():
                raise ValueError("Reminder title is required.")
            return "remind", parts[2].strip(), run_at
        raise ValueError("I do not understand that command.")

    @staticmethod
    def _existing_receipt(connection: sqlite3.Connection, receipt_key: str) -> TelegramReceipt:
        row = connection.execute(
            "SELECT payload_json FROM outbox WHERE idempotency_key = ?",
            (receipt_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError("duplicate Telegram update has no receipt outbox record")
        import json

        return TelegramReceipt(text=json.loads(row["payload_json"])["text"], duplicate=True)
