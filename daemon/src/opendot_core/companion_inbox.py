"""Deliver the companion's own messages (reminders, routines, the catch-up note) into the UI.

Jobs, routines and catch-up enqueue outbox messages for local destinations (``desktop:owner``,
``ui:owner``). Telegram and Slack have their own delivery workers; until now nothing delivered the
local ones, so they never reached the user. This worker moves each one into the "From your
companion" conversation, which the Chat screen lists with the others. It is idempotent: the message
id comes from the outbox row, so a crash between the insert and the outbox update cannot deliver
twice.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Callable

from .db import Database

INBOX_CONVERSATION_ID = "companion"
INBOX_TITLE = "From your companion"
LOCAL_PREFIXES = ("desktop:", "ui:")


def _now() -> datetime:
    return datetime.now(UTC)


def deliver_local(database: Database, *, clock: Callable[[], datetime] = _now, limit: int = 50) -> int:
    """Deliver pending local-destination outbox messages; returns how many were delivered."""
    database.migrate()
    delivered = 0
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id, destination, payload_json FROM outbox WHERE state = 'pending' "
            "AND (destination LIKE 'desktop:%' OR destination LIKE 'ui:%') ORDER BY rowid LIMIT ?",
            (limit,),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except ValueError:
            payload = {}
        text = str(payload.get("text") or "").strip()
        when = clock().astimezone(UTC).isoformat()
        with database.connect() as connection:
            with database.transaction(connection):
                if text:
                    connection.execute(
                        "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at",
                        (INBOX_CONVERSATION_ID, INBOX_TITLE, when, when),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO conversation_messages (id, conversation_id, role, text, created_at) "
                        "VALUES (?, ?, 'assistant', ?, ?)",
                        (f"outbox_{row['id']}", INBOX_CONVERSATION_ID, text, when),
                    )
                changed = connection.execute(
                    "UPDATE outbox SET state = 'sent', sent_at = ?, last_error = NULL WHERE id = ? AND state = 'pending'",
                    (when, row["id"]),
                ).rowcount
                delivered += int(changed == 1 and bool(text))
    return delivered


__all__ = ["INBOX_CONVERSATION_ID", "INBOX_TITLE", "LOCAL_PREFIXES", "deliver_local"]
