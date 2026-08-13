"""Answer free-form Telegram messages with Hermes, over Alfred's own transport.

Decision 1 puts the conversation loop in Hermes and the data in Alfred Core,
connected over MCP. Hermes also ships its own Telegram gateway, which would
normally own the chat channel -- but that adapter does not work here (it
hangs during its initial connect on this platform), while Alfred Core's own
Telegram poller and outbox do. This module bridges that gap without giving
up either half: Alfred keeps owning the Telegram transport it already runs
reliably, and Hermes keeps doing every bit of the actual understanding and
tool-calling, invoked as a one-shot subprocess (``hermes -p <profile> -z
<text>``).

Two-phase by necessity, not preference. ``TelegramGateway.handle()`` does its
work inside a ``BEGIN IMMEDIATE`` transaction, and a Hermes turn takes
seconds and spawns ``alfred-mcp`` against this same SQLite file. Calling
Hermes from inside that transaction would have Alfred holding the write lock
while Hermes's own MCP calls block on it and time out. So intake only marks
the message (``agent_deferred`` in its event metadata) and acknowledges it;
this module later picks that marker up with no transaction held, calls
Hermes, and enqueues the answer as a second outbox message.

Failure is fail-closed and visible: a timeout, a non-zero exit, or empty
output still enqueues a reply -- an honest "I couldn't reach my model" one --
under the same idempotency key the success path would have used. That is
deliberate. Leaving the key unclaimed would re-run an expensive model call
every cycle forever, and silently dropping it would leave the operator
staring at an unanswered "Thinking…".
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Protocol

from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .db import Database
from .outbox import Outbox

#: Telegram rejects a sendMessage payload over 4096 characters outright, so a
#: long agent answer is truncated rather than lost to a failed delivery.
TELEGRAM_MAX_MESSAGE_CHARS = 4096

_TRUNCATION_NOTE = "\n\n[truncated]"


class AgentRunResult(BaseModel):
    """One completed agent invocation, successful or not."""

    text: str
    ok: bool
    detail: str = ""


class AgentRunner(Protocol):
    def __call__(self, prompt: str) -> AgentRunResult: ...


class HermesBridgeReceipt(BaseModel):
    outcome: str  # "answered" | "failed"
    update_id: str
    reply_chars: int = 0


class HermesBridgeResult(BaseModel):
    pending: int
    answered: int
    failed: int


class SubprocessAgentRunner:
    """Run one Hermes turn as a child process and return its stdout.

    Nothing else in this codebase shells out, so the conventions are set
    here: an explicit timeout (a hung model must not wedge the run loop),
    explicit UTF-8 decoding (Hermes emits em dashes and emoji, and Windows
    would otherwise decode them with the ANSI codepage), and stdout only --
    Hermes writes warnings and progress to stderr, which is captured for the
    audit trail but never shown to the operator as an answer.
    """

    def __init__(
        self,
        *,
        command: str = "hermes",
        profile: str,
        timeout_seconds: float = 180.0,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.command = command
        self.profile = profile
        self.timeout_seconds = timeout_seconds
        self._runner = runner

    def __call__(self, prompt: str) -> AgentRunResult:
        argv = [self.command, "-p", self.profile, "-z", prompt]
        try:
            completed = self._runner(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return AgentRunResult(
                text="", ok=False, detail=f"agent timed out after {self.timeout_seconds:.0f}s"
            )
        except OSError as error:
            # Most often the binary is not on PATH -- a real possibility when
            # the run loop is the Windows service rather than a login shell.
            return AgentRunResult(text="", ok=False, detail=f"{error.__class__.__name__}: {error}")

        stdout = (completed.stdout or "").strip()
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            return AgentRunResult(
                text="", ok=False, detail=f"exit {completed.returncode}: {stderr[-300:]}"
            )
        if not stdout:
            return AgentRunResult(text="", ok=False, detail="agent produced no output")
        return AgentRunResult(text=stdout, ok=True)


class HermesBridge:
    """Answer messages that intake deferred, one agent turn at a time."""

    connector_name = "hermes_bridge"
    failure_reply = "I couldn't reach my language model just now, so I haven't answered that yet."

    def __init__(
        self,
        database: Database,
        agent: AgentRunner,
        *,
        lookback_seconds: float = 900.0,
        max_per_run: int = 3,
    ) -> None:
        self.database = database
        self.agent = agent
        self.lookback_seconds = lookback_seconds
        self.max_per_run = max_per_run

    def run_once(self) -> HermesBridgeResult:
        """Answer up to ``max_per_run`` deferred messages; never raises for one bad turn."""
        self.database.migrate()
        pending = self._pending()
        answered = 0
        failed = 0
        for event in pending:
            receipt = self._answer(event)
            if receipt.outcome == "answered":
                answered += 1
            else:
                failed += 1
        return HermesBridgeResult(pending=len(pending), answered=answered, failed=failed)

    def _pending(self) -> list[dict[str, Any]]:
        """Deferred messages still missing a reply, newest-eligible first.

        The lookback window is what keeps enabling this connector from firing
        a model call at every unanswered message ever received; only messages
        from the recent past are still worth answering.
        """
        cutoff = (datetime.now(UTC) - timedelta(seconds=self.lookback_seconds)).isoformat()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT e.external_id, e.content, e.metadata_json, e.occurred_at
                FROM events e
                WHERE e.source = 'telegram'
                  AND e.occurred_at >= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM outbox o
                      WHERE o.idempotency_key = 'hermes-reply:' || e.external_id
                  )
                ORDER BY e.occurred_at
                LIMIT ?
                """,
                (cutoff, self.max_per_run),
            ).fetchall()
        pending: list[dict[str, Any]] = []
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            # Filtered in Python rather than SQL so this does not depend on
            # the JSON1 extension being present in every SQLite build.
            if not metadata.get("agent_deferred"):
                continue
            chat_id = metadata.get("chat_id")
            if not isinstance(chat_id, int):
                # Intake always records chat_id, so this only happens for a
                # corrupt row. Skipping it here (rather than downstream) keeps
                # it from costing a model call or a repeated audit entry every
                # cycle -- there would be nowhere to send the answer anyway.
                continue
            pending.append(
                {
                    "external_id": row["external_id"],
                    "content": row["content"] or "",
                    "chat_id": chat_id,
                }
            )
        return pending

    def _answer(self, event: dict[str, Any]) -> HermesBridgeReceipt:
        external_id = str(event["external_id"])
        result = self.agent(event["content"])
        text = result.text if result.ok else self.failure_reply
        return self._store(
            external_id, chat_id=event["chat_id"], text=text, ok=result.ok, detail=result.detail
        )

    def _store(
        self, external_id: str, *, chat_id: int, text: str, ok: bool, detail: str
    ) -> HermesBridgeReceipt:
        reply = _truncate(text)
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                Outbox.enqueue(
                    connection,
                    destination=f"telegram:{chat_id}",
                    payload={"text": reply},
                    idempotency_key=f"hermes-reply:{external_id}",
                )
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="system:hermes_bridge",
                        client="hermes",
                        tool="hermes_bridge",
                        outcome="ok" if ok else "error",
                        result={"update_id": external_id, "reply_chars": str(len(reply)), "detail": detail},
                    ),
                )
        return HermesBridgeReceipt(
            outcome="answered" if ok else "failed", update_id=external_id, reply_chars=len(reply)
        )


def _truncate(text: str, *, limit: int = TELEGRAM_MAX_MESSAGE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(_TRUNCATION_NOTE)] + _TRUNCATION_NOTE
