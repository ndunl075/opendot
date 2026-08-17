"""Paired Slack Socket Mode intake and conservative outbox delivery.

Slack Socket Mode is intentionally limited to direct, locally paired message
surfaces.  The Socket Mode SDK owns the authenticated WebSocket and reconnects;
this module owns Alfred's event provenance, pairing, idempotency, and outbox.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .db import Database
from .events import EventStore
from .outbox import Outbox
from .quiet_hours import QuietHours
from .reminders import ReminderStore
from .tasks import TaskStore


class SlackEvent(BaseModel):
    type: str
    user: str | None = None
    channel: str | None = None
    text: str | None = None
    ts: str | None = None
    subtype: str | None = None
    bot_id: str | None = None


class SlackReceipt(BaseModel):
    text: str
    task_id: str | None = None
    reminder_job_id: str | None = None
    duplicate: bool = False
    ignored: bool = False


@dataclass(frozen=True)
class SlackPair:
    channel_id: str
    user_id: str


class SlackTransport(Protocol):
    def post_message(self, *, channel_id: str, text: str) -> str: ...


class SlackGateway:
    """Translate a paired human Slack message into a durable Alfred intent."""

    def __init__(self, database: Database, allowed_pairs: set[SlackPair]) -> None:
        self.database = database
        self.allowed_pairs = allowed_pairs

    def handle_event(self, *, event_id: str, event: SlackEvent, occurred_at: datetime | None = None) -> SlackReceipt:
        if event.type != "message" or event.subtype is not None or event.bot_id is not None:
            return SlackReceipt(text="Ignored non-user Slack event.", ignored=True)
        if not event.user or not event.channel or not event.text or not event.ts:
            return SlackReceipt(text="Ignored incomplete Slack message event.", ignored=True)
        if SlackPair(channel_id=event.channel, user_id=event.user) not in self.allowed_pairs:
            raise PermissionError("Slack sender is not locally paired with Alfred")

        self.database.migrate()
        timestamp = occurred_at or datetime.now(UTC)
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                stored_event = EventStore.append(
                    connection,
                    source="slack",
                    external_id=event_id,
                    occurred_at=timestamp,
                    content=event.text,
                    metadata={"channel_id": event.channel, "user_id": event.user, "message_ts": event.ts},
                )
                receipt_key = f"slack-receipt:{event_id}"
                if not stored_event.is_new:
                    return self._existing_receipt(connection, receipt_key)
                receipt = self._handle_new_event(connection, stored_event.id, event.text, event.channel, event_id)
                Outbox.enqueue(
                    connection,
                    destination=f"slack:{event.channel}",
                    payload={"text": receipt.text},
                    idempotency_key=receipt_key,
                )
                return receipt

    def _handle_new_event(
        self, connection: sqlite3.Connection, event_id: str, text: str, channel_id: str, slack_event_id: str
    ) -> SlackReceipt:
        try:
            command, title, run_at = self._parse_command(text)
        except ValueError as error:
            return SlackReceipt(text=f"{error} Use /task <title> or /remind <ISO-8601 time> <title>.")
        task = TaskStore.create(connection, title=title, source_event_id=event_id, due_at=run_at)
        if command == "task":
            return SlackReceipt(text=f"Saved task: {task.title}", task_id=task.id)
        reminder = ReminderStore.create(
            connection,
            run_at=run_at,
            task_id=task.id,
            destination=f"slack:{channel_id}",
            text=task.title,
            idempotency_key=f"slack-reminder:{slack_event_id}",
        )
        return SlackReceipt(
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
    def _existing_receipt(connection: sqlite3.Connection, receipt_key: str) -> SlackReceipt:
        row = connection.execute("SELECT payload_json FROM outbox WHERE idempotency_key = ?", (receipt_key,)).fetchone()
        if row is None:
            raise RuntimeError("duplicate Slack event has no receipt outbox record")
        return SlackReceipt(text=json.loads(row["payload_json"])["text"], duplicate=True)


class SlackOutboxWorker:
    """Deliver only to locally allowed Slack channels; never retry ambiguity."""

    destination_pattern = re.compile(r"^slack:([A-Z0-9]+)$")

    def __init__(
        self,
        database: Database,
        transport: SlackTransport,
        allowed_channel_ids: set[str],
        quiet_hours: QuietHours | None = None,
    ) -> None:
        self.database = database
        self.transport = transport
        self.allowed_channel_ids = allowed_channel_ids
        self.quiet_hours = quiet_hours or QuietHours.disabled()

    def deliver_pending(
        self, *, limit: int = 20, now: datetime | None = None
    ) -> list[dict[str, str]]:
        self.database.migrate()
        results: list[dict[str, str]] = []
        hold_jobs = self.quiet_hours.holds_job_deliveries(now)
        for _ in range(limit):
            claimed = self._claim_next(hold_job_deliveries=hold_jobs)
            if claimed is None:
                break
            outbox_id, destination, payload = claimed
            match = self.destination_pattern.fullmatch(destination)
            if match is None or match.group(1) not in self.allowed_channel_ids:
                results.append(self._fail(outbox_id, "destination is not a locally allowed Slack channel"))
                continue
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                results.append(self._fail(outbox_id, "outbox payload has no text message"))
                continue
            try:
                message_id = self.transport.post_message(channel_id=match.group(1), text=text)
            except Exception as error:
                results.append(self._fail(outbox_id, f"Slack send failed: {error.__class__.__name__}"))
                continue
            with self.database.connect() as connection:
                with self.database.transaction(connection):
                    connection.execute(
                        "UPDATE outbox SET state = 'sent', sent_at = ?, last_error = NULL WHERE id = ? AND state = 'sending'",
                        (datetime.now(UTC).isoformat(), outbox_id),
                    )
                    AuditLog.append_in_transaction(
                        connection,
                        AuditEvent(
                            actor="system:slack", client="slack", tool="slack_send", outcome="sent",
                            result={"outbox_id": outbox_id, "slack_message_id": message_id}, correlation_id=outbox_id,
                        ),
                    )
            results.append({"outbox_id": outbox_id, "state": "sent", "slack_message_id": message_id})
        return results

    def _claim_next(self, *, hold_job_deliveries: bool) -> tuple[str, str, dict[str, Any]] | None:
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                # Tie-broken by rowid (insertion order), never by id: id is a
                # random uuid4, and created_at only has second granularity, so
                # ordering by it scrambled any set of messages enqueued in the
                # same second. Telegram shipped exactly that bug -- a four-part
                # agent answer arriving with its closing question first -- and
                # this path had the same defect until it was fixed here too.
                if hold_job_deliveries:
                    row = connection.execute(
                        """
                        SELECT id, destination, payload_json FROM outbox
                        WHERE state = 'pending' AND destination LIKE 'slack:%' AND job_id IS NULL
                        ORDER BY created_at, rowid LIMIT 1
                        """
                    ).fetchone()
                else:
                    row = connection.execute(
                        """
                        SELECT id, destination, payload_json FROM outbox
                        WHERE state = 'pending' AND destination LIKE 'slack:%'
                        ORDER BY created_at, rowid LIMIT 1
                        """
                    ).fetchone()
                if row is None:
                    return None
                if connection.execute(
                    "UPDATE outbox SET state = 'sending', attempts = attempts + 1 WHERE id = ? AND state = 'pending'",
                    (row["id"],),
                ).rowcount != 1:
                    return None
                return row["id"], row["destination"], json.loads(row["payload_json"])

    def _fail(self, outbox_id: str, reason: str) -> dict[str, str]:
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute("UPDATE outbox SET state = 'failed', last_error = ? WHERE id = ? AND state = 'sending'", (reason, outbox_id))
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(actor="system:slack", client="slack", tool="slack_send", outcome="failed", result={"outbox_id": outbox_id, "reason": reason}, correlation_id=outbox_id),
                )
        return {"outbox_id": outbox_id, "state": "failed", "error": reason}
