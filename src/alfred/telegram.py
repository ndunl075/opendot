"""Telegram update translation without a network dependency or delivery side effect."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .audit import AuditEvent, AuditLog
from .db import Database
from .events import EventStore
from .outbox import Outbox
from .response_feedback import ResponseFeedbackService
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


class TelegramCallbackMessage(BaseModel):
    message_id: int
    chat: TelegramChat


class TelegramCallbackQuery(BaseModel):
    id: str
    sender: TelegramUser = Field(alias="from")
    message: TelegramCallbackMessage | None = None
    data: str | None = None


class TelegramUpdate(BaseModel):
    update_id: int
    message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None


class TelegramReceipt(BaseModel):
    text: str
    task_id: str | None = None
    reminder_job_id: str | None = None
    duplicate: bool = False
    ignored: bool = False
    agent_deferred: bool = False
    feedback_recorded: bool = False


@dataclass(frozen=True)
class TelegramPair:
    chat_id: int
    user_id: int


@dataclass(frozen=True)
class ParsedCommand:
    command: str
    title: str
    due_at: datetime | None = None
    remind_at: datetime | None = None


_NATURAL_DUE_REMINDER = re.compile(
    r"^\s*my\s+(?P<title>.+?)\s+is\s+due\s+(?P<due>monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"\s*[;,.]?\s*remind\s+me\s+(?P<remind>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_FEEDBACK_CALLBACK = re.compile(r"^af:(?P<response_update_id>\d+):(?P<code>[hmw])$")
_FEEDBACK_OUTCOME = {
    "h": "helpful",
    "m": "missing_context",
    "w": "wrong_context",
}
_ACTION_CALLBACK = re.compile(
    r"^aa:(?P<approval_id>[0-9a-f]{8}-[0-9a-f-]{27}):(?P<code>[yn])$",
    re.IGNORECASE,
)


class TelegramGateway:
    """Accept updates from locally paired identities and create durable intents."""

    #: Fallback acknowledgement when the message doesn't match a known topic.
    agent_ack_text = "one sec"

    #: Topic-specific acknowledgements, checked in order, covering every tool
    #: on the MCP surface plus the connectors that feed them. This is a
    #: keyword match, not a model call, on purpose: the ack is produced inside
    #: the intake write transaction and has to be instant, which is the same
    #: reason the real answer is deferred to `hermes_bridge` at all. Phrased
    #: to say what is being worked on, never to promise a result, since the
    #: agent decides for itself which tools it actually needs.
    #:
    #: Order matters. Action phrasing comes first so "schedule a meeting"
    #: (a write) doesn't get answered like "what's my schedule" (a read), and
    #: "draft an email" doesn't read as an inbox lookup. Every write below is
    #: still preview-then-approve; the ack says work is starting, never that
    #: anything was sent.
    #: Action phrasing, checked before the read topics it overlaps with so
    #: "schedule a meeting" isn't answered like "what's my schedule". These
    #: are single-intent and return on their own. Every write here is still
    #: preview-then-approve, so the wording says work is starting and never
    #: that anything was sent, created, or deleted.
    agent_ack_actions: tuple[tuple[tuple[str, ...], str], ...] = (
        (("search the web", "search online", "look it up", "look up online", "look online"),
         "searching the web..."),
        (("draft", "reply to", "respond to", "send an email", "send email", "email him", "email her", "email them"),
         "drafting that..."),
        (("schedule a", "schedule an", "book a", "book an", "put on my calendar", "add to my calendar",
          "add to calendar", "set up a meeting"), "setting that up..."),
        (("open an issue", "file an issue", "create an issue", "make an issue"), "writing that issue..."),
        (("forget", "delete that", "scrub", "wipe"), "on it..."),
        (("remind",), "on it..."),
        (("add a task", "new task", "add task"), "adding that..."),
    )

    #: Read topics as (keywords, noun). Unlike the actions above, *all*
    #: matches are collected and named together: "inbox and github today"
    #: used to answer "checking github..." alone, which read as if half the
    #: question had been missed.
    agent_ack_reads: tuple[tuple[tuple[str, ...], str], ...] = (
        (("canvas", "assignment", "homework", "syllabus", "coursework", "class", "course", "exam", "quiz"), "canvas"),
        (("github", "repo", "pull request", " pr ", " prs ", "ci ", "commit", "issue"), "github"),
        (("slack",), "slack"),
        (("steps", "sleep", "heart rate", "workout", "health", "fitness"), "your health data"),
        (("note", "notes", "obsidian", "vault"), "your notes"),
        (("inbox", "email", "e-mail", "gmail", "mail", "unread"), "your inbox"),
        (("task", "todo", "to-do", "to do"), "your tasks"),
        (("week", "review", "recap"), "your week"),
        (("agenda", "schedule", "calendar", "today", "tomorrow", "due", "meeting", "brief"), "your agenda"),
        (("remember", "recall", "memory", "did i say", "did i tell", "about me", "who am i", "my profile"),
         "what i know"),
        (("connector", "synced", "sync status", "connected", "working", "status", "everything ok", "everything okay"),
         "your connections"),
    )

    #: Two is the cap. "checking a, b and c..." stops sounding like a person.
    agent_ack_max_topics = 2

    @classmethod
    def acknowledgement_for(cls, text: str) -> str:
        """Name what's being looked at, in the order the message mentions it."""
        # Padded so " pr " and "ci " match whole words rather than firing on
        # "prepare" or "specific".
        haystack = f" {text.lower().strip()} "
        for keywords, ack in cls.agent_ack_actions:
            if any(keyword in haystack for keyword in keywords):
                return ack

        matched: list[tuple[int, str]] = []
        for keywords, noun in cls.agent_ack_reads:
            positions = [haystack.find(keyword) for keyword in keywords if keyword in haystack]
            if positions:
                matched.append((min(positions), noun))
        if not matched:
            return cls.agent_ack_text
        # Ordered by where each topic appears, so the ack echoes the question
        # back the way it was asked.
        nouns = [noun for _, noun in sorted(matched)][: cls.agent_ack_max_topics]
        return f"checking {' and '.join(nouns)}..."

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
        if update.callback_query is not None:
            data = update.callback_query.data or ""
            if _ACTION_CALLBACK.fullmatch(data):
                return self._handle_action_callback(update)
            return self._handle_feedback_callback(update)
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
            parsed: ParsedCommand | None = self._parse_command(text, received_at=datetime.fromtimestamp(message.date, UTC).astimezone())
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
                    text=text,
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

    def _handle_feedback_callback(self, update: TelegramUpdate) -> TelegramReceipt:
        callback = update.callback_query
        if callback is None or callback.message is None or not callback.data:
            raise ValueError("Telegram feedback callback is incomplete")
        pair = TelegramPair(
            chat_id=callback.message.chat.id,
            user_id=callback.sender.id,
        )
        if pair not in self.allowed_pairs:
            raise PermissionError("Telegram sender is not locally paired with Alfred")
        match = _FEEDBACK_CALLBACK.fullmatch(callback.data)
        if match is None:
            raise ValueError("Telegram callback is not an Alfred feedback action")
        response_update_id = match.group("response_update_id")
        outcome = _FEEDBACK_OUTCOME[match.group("code")]

        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                target = connection.execute(
                    """
                    SELECT metadata_json FROM events
                    WHERE source = 'telegram' AND external_id = ?
                    """,
                    (response_update_id,),
                ).fetchone()
                context = connection.execute(
                    """
                    SELECT 1 FROM response_context WHERE response_update_id = ?
                    """,
                    (response_update_id,),
                ).fetchone()
                if target is None or context is None:
                    raise ValueError("Telegram feedback target is unavailable")
                target_metadata = json.loads(target["metadata_json"])
                if (
                    target_metadata.get("chat_id") != pair.chat_id
                    or target_metadata.get("user_id") != pair.user_id
                    or not target_metadata.get("agent_deferred")
                ):
                    raise PermissionError("Telegram feedback target does not belong to this paired sender")

                stored_event = EventStore.append(
                    connection,
                    source="telegram",
                    external_id=str(update.update_id),
                    occurred_at=datetime.now(UTC),
                    content="response feedback",
                    metadata={
                        "chat_id": pair.chat_id,
                        "user_id": pair.user_id,
                        "feedback_callback": True,
                        "response_update_id": response_update_id,
                        "outcome": outcome,
                    },
                )
                if not stored_event.is_new:
                    return TelegramReceipt(text="feedback already saved", duplicate=True)
                feedback = ResponseFeedbackService.record_feedback_in_transaction(
                    connection,
                    callback_query_id=callback.id,
                    feedback_update_id=str(update.update_id),
                    response_update_id=response_update_id,
                    outcome=outcome,
                )
                if not feedback.recorded:
                    return TelegramReceipt(text="feedback already saved", duplicate=True)
                messages = {
                    "helpful": "thanks, that helps",
                    "missing_context": "got it. i'll track that as missing context",
                    "wrong_context": "got it. i'll be more careful with that context",
                }
                return TelegramReceipt(
                    text=messages[outcome],
                    feedback_recorded=True,
                )

    def _handle_action_callback(self, update: TelegramUpdate) -> TelegramReceipt:
        callback = update.callback_query
        if callback is None or callback.message is None or not callback.data:
            raise ValueError("Telegram action callback is incomplete")
        pair = TelegramPair(chat_id=callback.message.chat.id, user_id=callback.sender.id)
        if pair not in self.allowed_pairs:
            raise PermissionError("Telegram sender is not locally paired with Alfred")
        match = _ACTION_CALLBACK.fullmatch(callback.data)
        if match is None:
            raise ValueError("Telegram callback is not an Alfred action")
        approval_id = match.group("approval_id")
        decision = "approve" if match.group("code").lower() == "y" else "reject"

        self.database.migrate()
        now = datetime.now(UTC)
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                link = connection.execute(
                    """
                    SELECT l.*, a.state, a.expires_at
                    FROM telegram_action_links l
                    JOIN approvals a ON a.id = l.approval_id
                    WHERE l.approval_id = ?
                    """,
                    (approval_id,),
                ).fetchone()
                if link is None:
                    raise ValueError("Telegram action target is unavailable")
                if link["chat_id"] != pair.chat_id or link["user_id"] != pair.user_id:
                    raise PermissionError("Telegram action does not belong to this paired sender")
                if link["state"] != "pending":
                    return TelegramReceipt(text=f"that action is already {link['state']}", duplicate=True)
                if datetime.fromisoformat(link["expires_at"]) <= now:
                    return TelegramReceipt(text="that approval expired. ask me again", duplicate=True)

                stored_event = EventStore.append(
                    connection,
                    source="telegram",
                    external_id=str(update.update_id),
                    occurred_at=now,
                    content="action decision",
                    metadata={
                        "chat_id": pair.chat_id,
                        "user_id": pair.user_id,
                        "action_callback": True,
                        "approval_id": approval_id,
                        "decision": decision,
                    },
                )
                if not stored_event.is_new:
                    return TelegramReceipt(text="decision already received", duplicate=True)
                inserted = connection.execute(
                    """
                    INSERT INTO telegram_action_intents (
                        approval_id, callback_query_id, feedback_update_id,
                        decision, state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                    ON CONFLICT(approval_id) DO NOTHING
                    """,
                    (approval_id, callback.id, str(update.update_id), decision, now.isoformat(), now.isoformat()),
                ).rowcount
                if inserted != 1:
                    return TelegramReceipt(text="decision already received", duplicate=True)
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="owner:telegram",
                        client="telegram",
                        tool="telegram_action_decision",
                        outcome="recorded",
                        result={"approval_id": approval_id, "decision": decision},
                        correlation_id=approval_id,
                    ),
                )
                return TelegramReceipt(
                    text="approved. doing it now" if decision == "approve" else "cancelled"
                )

    def _handle_new_event(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        *,
        parsed: ParsedCommand | None,
        parse_error: str | None,
        deferred: bool,
        text: str,
        chat_id: int,
        update_id: int,
    ) -> TelegramReceipt:
        if parsed is None:
            if deferred:
                # Deliberately only an acknowledgement: the agent turn takes
                # seconds and must not run inside this write transaction, so
                # `hermes_bridge` sends the real answer as a second message.
                return TelegramReceipt(text=self.acknowledgement_for(text), agent_deferred=True)
            return TelegramReceipt(text=f"{parse_error} Use /task <title> or /remind <ISO-8601 time> <title>.")

        task = TaskStore.create(connection, title=parsed.title, source_event_id=event_id, due_at=parsed.due_at)
        if parsed.command == "task":
            return TelegramReceipt(text=f"Saved task: {task.title}", task_id=task.id)
        run_at = parsed.remind_at or parsed.due_at
        if run_at is None:
            raise ValueError("reminder time is required")
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
    def _parse_command(text: str, *, received_at: datetime | None = None) -> ParsedCommand:
        normalized = text.strip()
        if normalized.startswith("/task"):
            title = normalized.removeprefix("/task").strip()
            if title:
                return ParsedCommand("task", title)
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
            return ParsedCommand("remind", parts[2].strip(), due_at=run_at, remind_at=run_at)
        natural = _NATURAL_DUE_REMINDER.match(normalized)
        if natural:
            base = (received_at or datetime.now().astimezone()).astimezone()
            due_weekday = _WEEKDAYS[natural.group("due").casefold()]
            days_until_due = (due_weekday - base.weekday()) % 7
            if days_until_due == 0:
                days_until_due = 7
            due_day = base.date() + timedelta(days=days_until_due)
            due_at = datetime.combine(due_day, time(23, 59), tzinfo=base.tzinfo)
            reminder_weekday = _WEEKDAYS[natural.group("remind").casefold()]
            days_before = (due_weekday - reminder_weekday) % 7
            if days_before == 0:
                days_before = 7
            remind_day = due_day - timedelta(days=days_before)
            remind_at = datetime.combine(remind_day, time(9, 0), tzinfo=base.tzinfo)
            if remind_at <= base:
                raise ValueError("Reminder time has already passed.")
            return ParsedCommand(
                "remind",
                natural.group("title").strip(),
                due_at=due_at,
                remind_at=remind_at,
            )
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
