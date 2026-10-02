"""Persisted conversations (migration 0023) and ``GET /v1/chat/conversations[/{id}]``.

``ChatHub`` calls the ``ConversationRecorder`` (set as ``hub.recorder`` when this module is mounted):
the user's message when a task starts, and the assistant's reply (final text with its usage stamp,
tool calls and any approval card) as the task's events are published.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ...db import Database
from ..events import assistant_id
from ..models import (
    API_VERSION,
    ChatMessage,
    Conversation,
    ConversationList,
    ConversationSummary,
    ToolCallRecord,
    UsageStamp,
)
from ._common import error, parse_time, reply, utcnow

PRIOR_TURNS = 20
"""Earlier turns a new chat message carries; longer conversations are compacted by the agent loop."""

if TYPE_CHECKING:
    from . import ApiContext

_TITLE_LIMIT = 60


def _title(text: str) -> str:
    text = " ".join(text.split())
    return (text if len(text) <= _TITLE_LIMIT else text[: _TITLE_LIMIT - 1].rstrip() + "…") or "New conversation"


class ConversationRecorder:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.database.migrate()
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, str], dict[str, Any]] = {}

    # -- writes ---------------------------------------------------------------------------

    def add_user_message(self, conversation_id: str, message_id: str, text: str, task_id: str | None) -> None:
        now = utcnow().isoformat()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at",
                    (conversation_id, _title(text), now, now),
                )
                connection.execute(
                    "INSERT INTO conversation_messages (id, conversation_id, role, text, created_at, task_id) "
                    "VALUES (?, ?, 'user', ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                    (message_id, conversation_id, text, now, task_id),
                )

    def recent_turns(self, conversation_id: str, limit: int = PRIOR_TURNS) -> list[tuple[str, str]]:
        """The last ``limit`` user and assistant turns with text, oldest first."""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT role, text FROM conversation_messages WHERE conversation_id = ? "
                "AND role IN ('user', 'assistant') AND text <> '' ORDER BY seq DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return [(row["role"], row["text"]) for row in reversed(rows)]

    def on_event(self, conversation_id: str, message_id: str, event: dict[str, Any]) -> None:
        """Fold one published stream event into the assistant reply for this message."""
        kind = event.get("type")
        key = (conversation_id, message_id)
        with self._lock:
            buffer = self._pending.setdefault(key, {"text": "", "tools": [], "approval_id": None, "usage": None})
            if kind == "text_delta":
                buffer["text"] += event.get("text", "")
                return
            if kind == "tool_call":
                call = event.get("call") or {}
                buffer["tools"].append(call)
                return
            if kind == "approval_required":
                buffer["approval_id"] = (event.get("approval") or {}).get("id")
            elif kind == "completed":
                buffer["text"] = event.get("text") or buffer["text"]
                buffer["usage"] = event.get("usage")
            elif kind not in ("paused", "error"):
                return
            snapshot = dict(buffer)
            if kind in ("completed", "error"):
                self._pending.pop(key, None)
        if snapshot["text"] or snapshot["tools"] or snapshot["approval_id"]:
            self._write_assistant(conversation_id, message_id, snapshot)

    def _write_assistant(self, conversation_id: str, message_id: str, buffer: dict[str, Any]) -> None:
        now = utcnow().isoformat()
        usage = json.dumps(buffer["usage"]) if buffer["usage"] else None
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at",
                    (conversation_id, "New conversation", now, now),
                )
                task = connection.execute(
                    "SELECT task_id FROM conversation_messages WHERE id = ?", (message_id,)
                ).fetchone()
                connection.execute(
                    "INSERT INTO conversation_messages "
                    "(id, conversation_id, role, text, created_at, task_id, tool_calls_json, approval_id, usage_json) "
                    "VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET text = excluded.text, tool_calls_json = excluded.tool_calls_json, "
                    "approval_id = COALESCE(excluded.approval_id, conversation_messages.approval_id), "
                    "usage_json = COALESCE(excluded.usage_json, conversation_messages.usage_json)",
                    (
                        assistant_id(message_id), conversation_id, buffer["text"], now,
                        task["task_id"] if task else None, json.dumps(buffer["tools"]), buffer["approval_id"], usage,
                    ),
                )
                connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))

    # -- reads ----------------------------------------------------------------------------

    def list_all(self) -> ConversationList:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT c.id, c.title, c.updated_at, "
                "(SELECT COUNT(*) FROM conversation_messages m WHERE m.conversation_id = c.id) AS n "
                "FROM conversations c ORDER BY c.updated_at DESC, c.id"
            ).fetchall()
        return ConversationList(
            conversations=[
                ConversationSummary(id=r["id"], title=r["title"], updated_at=_when(r["updated_at"]), message_count=r["n"])
                for r in rows
            ]
        )

    def get(self, conversation_id: str, *, paused: bool = False) -> Conversation | None:
        with self.database.connect() as connection:
            head = connection.execute("SELECT id, title FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if head is None:
                return None
            rows = connection.execute(
                "SELECT * FROM conversation_messages WHERE conversation_id = ? ORDER BY seq", (conversation_id,)
            ).fetchall()
        messages = [
            ChatMessage(
                id=r["id"],
                role=r["role"],
                text=r["text"],
                created_at=_when(r["created_at"]),
                tool_calls=[ToolCallRecord.model_validate(c) for c in json.loads(r["tool_calls_json"])],
                approval_id=r["approval_id"],
                usage=UsageStamp.model_validate(json.loads(r["usage_json"])) if r["usage_json"] else None,
            )
            for r in rows
        ]
        return Conversation(id=head["id"], title=head["title"], messages=messages, paused=paused)


def _when(value: str) -> datetime:
    return parse_time(value)  # type: ignore[return-value]


def routes(ctx: ApiContext) -> list[Route]:
    if ctx.database is None:
        return []
    recorder = ConversationRecorder(ctx.database)
    ctx.extras["conversations"] = recorder
    ctx.hub.recorder = recorder

    async def list_conversations(_: Request) -> Response:
        return reply(await asyncio.to_thread(recorder.list_all))

    async def get_conversation(request: Request) -> Response:
        paused = ctx.loop.paused() is not None
        found = await asyncio.to_thread(recorder.get, request.path_params["conversation_id"], paused=paused)
        if found is None:
            return error(404, "not_found", "No such conversation.")
        return reply(found)

    v = f"/{API_VERSION}/chat/conversations"
    return [
        Route(v, list_conversations, methods=["GET"]),
        Route(f"{v}/{{conversation_id}}", get_conversation, methods=["GET"]),
    ]
