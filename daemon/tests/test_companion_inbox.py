"""Local-destination outbox messages reach the UI as companion messages (M4)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from opendot_core.companion_inbox import INBOX_CONVERSATION_ID, deliver_local
from opendot_core.db import Database
from opendot_core.outbox import Outbox

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _enqueue(database: Database, destination: str, text: str, key: str) -> None:
    with database.connect() as connection:
        with database.transaction(connection):
            Outbox.enqueue(connection, destination=destination, payload={"text": text}, idempotency_key=key)


def test_reminders_and_notes_land_in_the_companion_conversation_once(tmp_path: Path) -> None:
    database = Database(tmp_path / "o.db")
    database.migrate()
    _enqueue(database, "desktop:owner", "Reminder: call mom", "r1")
    _enqueue(database, "ui:owner", "While your computer was asleep: 1 reminder", "c1")
    _enqueue(database, "telegram:4242424242", "not for the UI", "t1")
    assert deliver_local(database, clock=lambda: NOW) == 2
    assert deliver_local(database, clock=lambda: NOW) == 0  # idempotent
    with database.connect() as connection:
        texts = [row["text"] for row in connection.execute(
            "SELECT text FROM conversation_messages WHERE conversation_id = ? ORDER BY seq", (INBOX_CONVERSATION_ID,)
        )]
        states = {row["destination"]: row["state"] for row in connection.execute("SELECT destination, state FROM outbox")}
        title = connection.execute("SELECT title FROM conversations WHERE id = ?", (INBOX_CONVERSATION_ID,)).fetchone()
    assert texts == ["Reminder: call mom", "While your computer was asleep: 1 reminder"]
    assert states == {"desktop:owner": "sent", "ui:owner": "sent", "telegram:4242424242": "pending"}
    assert title["title"] == "From your companion"


def test_the_s1_reminder_is_delivered_to_the_ui(tmp_path: Path) -> None:
    from opendot_core.eval.fake_provider import text_turn, tool_turn
    from opendot_core.eval.harness import build_stack

    stack = build_stack(tmp_path, script=[tool_turn("reminder_set", {"text": "call mom", "minutes": 1}), text_turn("ok")])
    stack.start_and_run("remind me to call mom in a minute")
    stack.advance_time(minutes=1)
    stack.run_due_jobs()
    assert deliver_local(stack.database, clock=stack.clock) == 1
    with stack.database.connect() as connection:
        text = connection.execute("SELECT text FROM conversation_messages").fetchone()["text"]
    assert "call mom" in text
