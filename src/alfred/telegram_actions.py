"""Durable Telegram approvals for Alfred's existing safe action proposals."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from .action_executor import ActionExecutor
from .audit import AuditEvent, AuditLog
from .db import Database
from .outbox import Outbox
from .policy import ApprovalService
from .secret_store import SystemKeyringSecretStore


ACTION_LABELS = {
    "calendar_event_create": "calendar event",
    "gmail_draft_create": "email draft",
    "gmail_message_send": "send email",
    "github_issue_create": "GitHub issue",
    "memory_forget": "forget request",
}


def action_keyboard(
    approvals: list[tuple[str, str]],
    *,
    existing_rows: list[list[dict[str, str]]] | None = None,
) -> dict[str, list[list[dict[str, str]]]]:
    """Put consequential confirmation above the lower-priority feedback controls."""
    rows: list[list[dict[str, str]]] = []
    for approval_id, action_type in approvals[:3]:
        label = ACTION_LABELS.get(action_type, "action")
        rows.append(
            [
                {"text": f"approve {label}", "callback_data": f"aa:{approval_id}:y"},
                {"text": "cancel", "callback_data": f"aa:{approval_id}:n"},
            ]
        )
    rows.extend(existing_rows or [])
    return {"inline_keyboard": rows}


class TelegramActionWorker:
    """Execute button decisions outside Telegram intake's database transaction."""

    actor = "mcp:hermes"

    def __init__(
        self,
        database: Database,
        *,
        executor: Callable[[str, str, str], dict[str, Any]] | None = None,
        secret_store: SystemKeyringSecretStore | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.database = database
        self.approvals = ApprovalService(database)
        self.secrets = secret_store or SystemKeyringSecretStore()
        self.executor = executor or self._execute
        self.max_attempts = max_attempts

    def run_pending(self, *, limit: int = 5) -> int:
        self.database.migrate()
        handled = 0
        for _ in range(limit):
            intent = self._claim()
            if intent is None:
                break
            try:
                if intent["decision"] == "reject":
                    self.approvals.reject(intent["approval_id"], actor=self.actor)
                    message = "cancelled. nothing changed."
                else:
                    token = self._approval_token(intent["approval_id"])
                    approval = self.approvals.get(intent["approval_id"])
                    if approval is not None and approval.state == "pending":
                        self.approvals.approve_with_token(
                            intent["approval_id"], actor=self.actor, token=token
                        )
                    result = self.executor(intent["approval_id"], self.actor, token)
                    message = self._success_message(intent["action_type"], result)
                self._complete(intent, message)
                if intent["decision"] == "approve":
                    self.secrets.delete(self._secret_name(intent["approval_id"]))
                handled += 1
            except Exception as error:
                self._retry_or_fail(intent, error)
        return handled

    def _execute(self, approval_id: str, actor: str, token: str) -> dict[str, Any]:
        return ActionExecutor(self.database).execute(approval_id, actor=actor, token=token)

    def _approval_token(self, approval_id: str) -> str:
        name = self._secret_name(approval_id)
        existing = self.secrets.get_optional(name)
        if existing:
            return existing
        token = "alf_" + secrets.token_urlsafe(32)
        self.secrets.store(name, token)
        return token

    @staticmethod
    def _secret_name(approval_id: str) -> str:
        return f"telegram-approval-{approval_id}"

    def _claim(self) -> sqlite3.Row | None:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                row = connection.execute(
                    """
                    SELECT i.*, l.chat_id, l.action_type
                    FROM telegram_action_intents i
                    JOIN telegram_action_links l USING (approval_id)
                    WHERE i.state IN ('pending', 'running')
                      AND (i.next_attempt_at IS NULL OR i.next_attempt_at <= ?)
                    ORDER BY i.created_at
                    LIMIT 1
                    """,
                    (now,),
                ).fetchone()
                if row is None:
                    return None
                connection.execute(
                    """
                    UPDATE telegram_action_intents
                    SET state = 'running', attempts = attempts + 1, updated_at = ?
                    WHERE approval_id = ?
                    """,
                    (now, row["approval_id"]),
                )
                return row

    def _complete(self, intent: sqlite3.Row, message: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    UPDATE telegram_action_intents
                    SET state = 'completed', updated_at = ?, completed_at = ?, last_error = NULL
                    WHERE approval_id = ?
                    """,
                    (now, now, intent["approval_id"]),
                )
                Outbox.enqueue(
                    connection,
                    destination=f"telegram:{intent['chat_id']}",
                    payload={"text": message},
                    idempotency_key=f"telegram-action-result:{intent['approval_id']}",
                )
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="owner:telegram",
                        client="telegram",
                        tool="telegram_action",
                        outcome="completed",
                        result={
                            "approval_id": intent["approval_id"],
                            "decision": intent["decision"],
                            "action_type": intent["action_type"],
                        },
                        correlation_id=intent["approval_id"],
                    ),
                )

    def _retry_or_fail(self, intent: sqlite3.Row, error: Exception) -> None:
        attempts = int(intent["attempts"]) + 1
        final = attempts >= self.max_attempts
        now = datetime.now(UTC)
        retry_at = None if final else (now + timedelta(seconds=30 * attempts)).isoformat()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    UPDATE telegram_action_intents
                    SET state = ?, next_attempt_at = ?, last_error = ?, updated_at = ?
                    WHERE approval_id = ?
                    """,
                    (
                        "failed" if final else "pending",
                        retry_at,
                        error.__class__.__name__,
                        now.isoformat(),
                        intent["approval_id"],
                    ),
                )
                if final:
                    Outbox.enqueue(
                        connection,
                        destination=f"telegram:{intent['chat_id']}",
                        payload={"text": "I couldn't finish that action. nothing else was attempted."},
                        idempotency_key=f"telegram-action-failed:{intent['approval_id']}",
                    )

    @staticmethod
    def _success_message(action_type: str, result: dict[str, Any]) -> str:
        messages = {
            "calendar_event_create": "done — it’s on your calendar.",
            "gmail_draft_create": "done — the email draft is ready.",
            "gmail_message_send": "sent.",
            "github_issue_create": "done — the GitHub issue is open.",
            "memory_forget": "done — I forgot it.",
        }
        return messages.get(action_type, "done.")
