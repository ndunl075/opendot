"""Telegram update translation without a network dependency or delivery side effect."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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


@dataclass(frozen=True)
class TelegramPair:
    chat_id: int
    user_id: int


class TelegramGateway:
    """Accept updates from locally paired identities and create durable intents."""

    def __init__(self, database: Database, allowed_pairs: set[TelegramPair]) -> None:
        self.database = database
        self.allowed_pairs = allowed_pairs

    @classmethod
    def from_path(cls, database_path: Path | str, allowed_pairs: set[TelegramPair]) -> "TelegramGateway":
        return cls(Database(database_path), allowed_pairs)

    def handle(self, update: TelegramUpdate) -> TelegramReceipt:
        """Translate one update atomically; no network message is sent here."""
        if update.message is None or not update.message.text:
            return TelegramReceipt(text="Ignored non-text Telegram update.", ignored=True)
        message = update.message
        pair = TelegramPair(chat_id=message.chat.id, user_id=message.sender.id)
        if pair not in self.allowed_pairs:
            raise PermissionError("Telegram sender is not locally paired with Alfred")

        self.database.migrate()
        event_time = datetime.fromtimestamp(message.date, UTC)
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                stored_event = EventStore.append(
                    connection,
                    source="telegram",
                    external_id=str(update.update_id),
                    occurred_at=event_time,
                    content=message.text,
                    metadata={"chat_id": message.chat.id, "message_id": message.message_id, "user_id": message.sender.id},
                )
                receipt_key = f"telegram-receipt:{update.update_id}"
                if not stored_event.is_new:
                    return self._existing_receipt(connection, receipt_key)
                receipt = self._handle_new_event(connection, stored_event.id, message.text, message.chat.id, update.update_id)
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
        text: str,
        chat_id: int,
        update_id: int,
    ) -> TelegramReceipt:
        try:
            command, title, run_at = self._parse_command(text)
        except ValueError as error:
            return TelegramReceipt(text=f"{error} Use /task <title> or /remind <ISO-8601 time> <title>.")

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
