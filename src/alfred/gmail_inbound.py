"""Turn commanded email from allowed senders into local tasks/reminders.

This is the "inbound Alfred email" leg of section 9's Communications row: the
same pattern Telegram uses (paired identity -> parsed command -> task/
reminder), adapted to a subject line instead of a slash command, and to
Gmail's stable per-message ID instead of an update-offset cursor.

Default-deny channel identity applies here exactly as it does for Telegram:
only an explicitly allowed sender address may command Alfred. Unlike
Telegram's dedicated bot inbox, the unread Gmail inbox is mostly ordinary
mail, so a message that simply isn't a recognized command is silently
ignored rather than audited as a rejection -- only a *recognized* command
from a sender who is not allowed is treated as a rejection worth auditing.

Sending an email reply is a consequential, approval-gated write (see
GmailActions/GmailSendActions) and is never done from this ingest path. A
reminder created from a "Remind:" email can still be delivered, but only to
a destination this connector is explicitly configured with -- never back to
the sender's own address.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Any

from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .db import Database
from .events import EventStore
from .gmail import GmailTransport, parse_message_headers
from .reminders import ReminderStore
from .tasks import TaskStore


class GmailInboundReceipt(BaseModel):
    outcome: str  # "handled" | "duplicate" | "rejected" | "ignored"
    task_id: str | None = None
    reminder_job_id: str | None = None


class GmailInboundResult(BaseModel):
    received: int
    handled: int
    duplicate: int
    rejected: int
    ignored: int


class GmailInboundGateway:
    """Poll the unread inbox and act only on commands from allowed senders."""

    connector_name = "gmail_inbound"

    def __init__(
        self,
        database: Database,
        transport: GmailTransport,
        allowed_senders: set[str],
        *,
        default_reminder_destination: str | None = None,
    ) -> None:
        self.database = database
        self.transport = transport
        self.allowed_senders = {address.strip().lower() for address in allowed_senders}
        self.default_reminder_destination = default_reminder_destination

    def poll(self) -> GmailInboundResult:
        """Fetch the current unread inbox and process each message once.

        This is a second, independent unread-inbox fetch alongside
        ``GmailSync.sync()`` -- deliberately so: the two connectors have
        different jobs (read snapshot vs. command intake) and neither should
        need to know about the other's transport call.
        """
        self.database.migrate()
        messages = self.transport.list_unread_inbox()
        counts = {"handled": 0, "duplicate": 0, "rejected": 0, "ignored": 0}
        for item in messages:
            receipt = self.handle(item)
            counts[receipt.outcome] += 1
        return GmailInboundResult(received=len(messages), **counts)

    def handle(self, item: dict[str, Any]) -> GmailInboundReceipt:
        """Process one already-fetched Gmail message atomically."""
        self.database.migrate()
        message_id = item.get("id")
        internal_date = item.get("internalDate")
        if not isinstance(message_id, str) or not message_id or not isinstance(internal_date, str):
            raise ValueError("Gmail message is missing id or internalDate")
        headers = parse_message_headers(item.get("payload"))
        subject = headers.get("subject") or ""

        try:
            command, title, run_at = _parse_subject_command(subject)
        except ValueError:
            return GmailInboundReceipt(outcome="ignored")

        sender_email = parseaddr(headers.get("from") or "")[1].lower()
        if not sender_email or sender_email not in self.allowed_senders:
            AuditLog(self.database).append(
                AuditEvent(
                    actor="system:gmail_inbound",
                    client="gmail",
                    tool="gmail_inbound_command",
                    outcome="rejected",
                    result={"message_id": message_id, "reason": "sender is not a locally allowed command sender"},
                )
            )
            return GmailInboundReceipt(outcome="rejected")

        occurred_at = datetime.fromtimestamp(int(internal_date) / 1000, tz=UTC)
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                stored = EventStore.append(
                    connection,
                    source=self.connector_name,
                    external_id=message_id,
                    occurred_at=occurred_at,
                    content=subject,
                    metadata={"from": sender_email, "command": command},
                    sensitivity="personal",
                )
                if not stored.is_new:
                    return GmailInboundReceipt(outcome="duplicate")
                task = TaskStore.create(connection, title=title, source_event_id=stored.id, due_at=run_at)
                reminder_job_id = None
                if command == "remind" and self.default_reminder_destination:
                    reminder = ReminderStore.create(
                        connection,
                        run_at=run_at,
                        task_id=task.id,
                        destination=self.default_reminder_destination,
                        text=task.title,
                        idempotency_key=f"gmail-inbound-reminder:{message_id}",
                    )
                    reminder_job_id = reminder.id
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="system:gmail_inbound",
                        client="gmail",
                        tool="gmail_inbound_command",
                        outcome="ok",
                        result={
                            "message_id": message_id,
                            "command": command,
                            "task_id": task.id,
                            "reminder_job_id": reminder_job_id or "",
                        },
                    ),
                )
        return GmailInboundReceipt(outcome="handled", task_id=task.id, reminder_job_id=reminder_job_id)


def _parse_subject_command(subject: str) -> tuple[str, str, datetime | None]:
    """Parse a subject line the same way Telegram parses ``/task``/``/remind``.

    ``Task: <title>`` creates an open task. ``Remind: <ISO-8601> <title>``
    creates a task with that due date and, when a reminder destination is
    configured, schedules delivery there.
    """
    normalized = subject.strip()
    lowered = normalized.lower()
    if lowered.startswith("task:"):
        title = normalized[len("task:") :].strip()
        if not title:
            raise ValueError("task title is required")
        return "task", title, None
    if lowered.startswith("remind:"):
        remainder = normalized[len("remind:") :].strip()
        parts = remainder.split(maxsplit=1)
        if len(parts) != 2:
            raise ValueError("reminder needs a time and a title")
        try:
            run_at = datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("reminder time must be ISO-8601, for example 2026-08-14T09:00:00Z") from error
        if run_at.tzinfo is None:
            raise ValueError("reminder time must include a timezone")
        if not parts[1].strip():
            raise ValueError("reminder title is required")
        return "remind", parts[1].strip(), run_at
    raise ValueError("subject is not a recognized Alfred command")
