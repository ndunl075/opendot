"""Answer free-form Telegram messages with an injected agent callable.

OpenDot owns the Telegram transport, the local database and every deterministic
step of a turn; the model is one injected callable, ``Callable[[str],
AgentRunResult]``. This module is the driver between the two, and the skeleton
the real agent loop plugs into.

Two-phase by necessity, not preference. ``TelegramGateway.handle()`` does its
work inside a ``BEGIN IMMEDIATE`` transaction, and an agent turn takes seconds
and may itself open this same SQLite file. Calling the agent from inside that
transaction would have intake holding the write lock while the turn's own
database calls block on it and time out. So intake only marks the message
(``agent_deferred`` in its event metadata) and acknowledges it; this module
later picks that marker up with no transaction held, calls the agent, and
enqueues the answer as outbox messages.

Failure is fail-closed and visible: a failed agent turn (timeout, cap reached,
empty output) still enqueues a reply -- an honest "I hit a snag" one -- under
the same idempotency key the success path would have used. Leaving the key
unclaimed would re-run an expensive model call every cycle forever, and
silently dropping it would leave the owner staring at an unanswered message.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel

from ..audit import AuditEvent, AuditLog
from ..db import Database
from ..memory_graph import MemoryGraph
from ..models import Redactor
from ..outbox import Outbox
from ..response_feedback import ResponseFeedbackService
from ..telegram_actions import action_keyboard, action_preview
from ..workflow_learning import WorkflowObservationStore
from .caps import UsageCaps
from .context import DEFAULT_CONTEXT_CHAR_BUDGET, REPLY_KEY_PREFIX
from .planner import TurnPlan, plan_turn
from .redaction import redact_except_current_request
from .style import DEFAULT_MAX_BUBBLES, split_into_bubbles


class AgentRunResult(BaseModel):
    """One completed agent invocation, successful or not."""

    text: str
    ok: bool
    detail: str = ""
    duration_ms: int | None = None
    runtime: str = "unknown"
    tool_count: int | None = None
    cost_usd: float | None = None


class AgentRunner(Protocol):
    def __call__(self, prompt: str) -> AgentRunResult: ...


class AgentBridgeReceipt(BaseModel):
    outcome: str  # "answered" | "failed"
    update_id: str
    bubbles: int = 0
    reply_chars: int = 0


class AgentBridgeResult(BaseModel):
    pending: int
    answered: int
    failed: int


class AgentBridge:
    """Answer messages that intake deferred, one agent turn at a time."""

    connector_name = "agent_bridge"
    failure_reply = "i hit a snag before i could answer. try that again?"

    def __init__(
        self,
        database: Database,
        agent: AgentRunner,
        *,
        lookback_seconds: float = 900.0,
        max_per_run: int = 3,
        max_bubbles: int = DEFAULT_MAX_BUBBLES,
        context_char_budget: int = DEFAULT_CONTEXT_CHAR_BUDGET,
        memory_graph: MemoryGraph | None = None,
        monotonic: Callable[[], float] = time.perf_counter,
        telegram_transport: object | None = None,
        caps: UsageCaps | None = None,
        redact_outbound: bool = True,
    ) -> None:
        self.database = database
        self.agent = agent
        self.lookback_seconds = lookback_seconds
        self.max_per_run = max_per_run
        self.max_bubbles = max_bubbles
        self.context_char_budget = context_char_budget
        self.memory_graph = memory_graph or MemoryGraph(database)
        self._monotonic = monotonic
        #: Accepted so callers can hand over the Telegram transport they
        #: already hold; replies go through the outbox, never directly.
        self.telegram_transport = telegram_transport
        self.caps = caps
        self.redact_outbound = redact_outbound
        self._redactor = Redactor()

    def run_once(self) -> AgentBridgeResult:
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
        return AgentBridgeResult(pending=len(pending), answered=answered, failed=failed)

    def pending_chat_ids(self) -> frozenset[int]:
        """Return only chats whose recent deferred turns still need an answer."""

        self.database.migrate()
        return frozenset(int(event["chat_id"]) for event in self._pending())

    def _pending(self) -> list[dict[str, Any]]:
        """Deferred messages still missing a reply, oldest first.

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
                      -- Bubble 0 is always written, so its presence means the
                      -- whole answer was already stored (all bubbles are
                      -- enqueued in one transaction).
                      SELECT 1 FROM outbox o
                      WHERE o.idempotency_key = ? || ':' || e.external_id || ':0'
                  )
                ORDER BY e.occurred_at
                LIMIT ?
                """,
                (cutoff, REPLY_KEY_PREFIX, self.max_per_run),
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
                    "user_id": metadata.get("user_id"),
                    "occurred_at": row["occurred_at"],
                    "message_id": metadata.get("message_id"),
                }
            )
        return pending

    def _answer(self, event: dict[str, Any]) -> AgentBridgeReceipt:
        bridge_started_at = datetime.now(UTC).isoformat()
        bridge_started = self._monotonic()
        external_id = str(event["external_id"])
        request = str(event["content"])
        chat_id = int(event["chat_id"])

        context_started = self._monotonic()
        plan = plan_turn(
            self.database,
            request,
            chat_id=chat_id,
            external_id=external_id,
            memory_graph=self.memory_graph,
            context_char_budget=self.context_char_budget,
        )
        context_ms = max(0, round((self._monotonic() - context_started) * 1000))
        if plan.direct_answer is not None:
            result = AgentRunResult(
                text=plan.direct_answer,
                ok=True,
                detail="local calendar fast path",
                duration_ms=0,
                runtime="local",
                tool_count=0,
            )
            agent_ms = 0
        else:
            agent_started = self._monotonic()
            result = self._run_agent(plan)
            agent_ms = max(0, round((self._monotonic() - agent_started) * 1000))
        text = result.text if result.ok else self.failure_reply
        telemetry = {
            "timing_version": 1,
            "bridge_started_at": bridge_started_at,
            "context_ms": context_ms,
            "agent_ms": agent_ms,
            "agent_reported_ms": result.duration_ms,
            "response_ready_ms": max(0, round((self._monotonic() - bridge_started) * 1000)),
            "runtime": result.runtime,
            "tool_count": result.tool_count,
        }
        return self._store(
            external_id,
            chat_id=chat_id,
            text=text,
            ok=result.ok,
            detail=result.detail,
            telemetry=telemetry,
            context_trace=plan.context_trace,
            user_id=event.get("user_id"),
            approval_requested_since=bridge_started_at,
        )

    def _run_agent(self, plan: TurnPlan) -> AgentRunResult:
        """Call the injected agent on the planned prompt, within the usage caps.

        A plain callable is enough. An agent may additionally expose
        ``run_conversation(prompt)`` (used for a casual turn, which offers no
        tools) and ``run_scoped(prompt, allowed_tools=...)`` (used for a work
        turn, with the planned tool group).
        """
        if self.caps is not None:
            refusal = self.caps.refusal()
            if refusal is not None:
                # Nothing was called, so nothing is recorded against the cap.
                return AgentRunResult(text="", ok=False, detail=refusal)
        prompt = plan.prompt
        # The model boundary: everything stored about other people is scrubbed,
        # the owner's own current message is not (see redaction.py).
        if self.redact_outbound:
            prompt = redact_except_current_request(prompt, self._redactor)
        started = self._monotonic()
        result = self._dispatch(prompt, plan)
        if self.caps is not None:
            # Recorded after the call whether or not it succeeded: a failed
            # turn may still have cost money at the provider.
            self.caps.record_call(
                ok=result.ok,
                cost_usd=result.cost_usd,
                detail=result.detail,
                duration_ms=(
                    result.duration_ms
                    if result.duration_ms is not None
                    else max(0, round((self._monotonic() - started) * 1000))
                ),
            )
        return result

    def _dispatch(self, prompt: str, plan: TurnPlan) -> AgentRunResult:
        if plan.casual:
            run_conversation = getattr(self.agent, "run_conversation", None)
            if callable(run_conversation):
                return run_conversation(prompt)
        run_scoped = getattr(self.agent, "run_scoped", None)
        if callable(run_scoped):
            return run_scoped(prompt, allowed_tools=plan.allowed_tools)
        return self.agent(prompt)

    def _store(
        self,
        external_id: str,
        *,
        chat_id: int,
        text: str,
        ok: bool,
        detail: str,
        telemetry: dict[str, Any] | None = None,
        context_trace: dict[str, Any] | None = None,
        user_id: int | None = None,
        approval_requested_since: str | None = None,
    ) -> AgentBridgeReceipt:
        bubbles = split_into_bubbles(text, max_bubbles=self.max_bubbles)
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                WorkflowObservationStore.complete_turn_in_transaction(
                    connection,
                    external_id,
                    outcome="ok" if ok else "error",
                )
                approvals: list[tuple[str, str]] = []
                if ok:
                    trace = context_trace or {"sources": [], "freshness": {}, "items": []}
                    ResponseFeedbackService.record_context_in_transaction(
                        connection,
                        response_update_id=external_id,
                        sources=list(trace.get("sources") or []),
                        freshness=dict(trace.get("freshness") or {}),
                        items=list(trace.get("items") or []),
                    )
                    # An answer written from a connector that has not synced
                    # in a day was already missing something, and that is
                    # invisible from the chat: the reply reads fine, it just
                    # describes a state of the world that was lost track of.
                    # Recorded against the same trace the owner's own reaction
                    # would land on, as its own signal so neither overwrites
                    # the other.
                    ResponseFeedbackService.record_coverage_signal_in_transaction(
                        connection,
                        response_update_id=external_id,
                        sources=list(trace.get("sources") or []),
                        freshness=dict(trace.get("freshness") or {}),
                    )
                    if isinstance(user_id, int) and approval_requested_since:
                        rows = connection.execute(
                            """
                            SELECT id, action_type, preview_json FROM approvals
                            WHERE actor = 'mcp:agent'
                              AND state = 'pending'
                              AND requested_at >= ?
                            ORDER BY requested_at, id
                            LIMIT 3
                            """,
                            (approval_requested_since,),
                        ).fetchall()
                        now = datetime.now(UTC).isoformat()
                        approvals = [(str(row["id"]), str(row["action_type"])) for row in rows]
                        # Show the letter, not a summary of it. The agent's
                        # own prose named only the subject, so approving meant
                        # sending something never read. Rendered from the same
                        # record the executor consumes, so the preview and the
                        # send cannot disagree.
                        previews = [
                            preview
                            for row in rows
                            if (
                                preview := action_preview(
                                    str(row["action_type"]),
                                    json.loads(row["preview_json"] or "{}"),
                                )
                            )
                        ]
                        if previews:
                            bubbles = bubbles + previews
                        for approval_id, action_type in approvals:
                            connection.execute(
                                """
                                INSERT INTO telegram_action_links (
                                    approval_id, response_update_id, chat_id, user_id,
                                    action_type, created_at
                                ) VALUES (?, ?, ?, ?, ?, ?)
                                ON CONFLICT(approval_id) DO NOTHING
                                """,
                                (approval_id, external_id, chat_id, user_id, action_type, now),
                            )
                for index, bubble in enumerate(bubbles):
                    # Index 0 is what _pending()'s NOT EXISTS checks, so the
                    # whole set is claimed atomically with the first bubble.
                    payload: dict[str, Any] = {"text": bubble}
                    # Buttons are reserved for decisions the assistant is not
                    # allowed to make alone, so an ordinary reply arrives with
                    # no keyboard at all.
                    if ok and approvals and index == len(bubbles) - 1:
                        payload["reply_markup"] = action_keyboard(approvals)
                    Outbox.enqueue(
                        connection,
                        destination=f"telegram:{chat_id}",
                        payload=payload,
                        idempotency_key=f"{REPLY_KEY_PREFIX}:{external_id}:{index}",
                    )
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="system:agent_bridge",
                        client="agent",
                        tool="agent_bridge",
                        outcome="ok" if ok else "error",
                        result={
                            "update_id": external_id,
                            "bubbles": str(len(bubbles)),
                            "reply_chars": str(sum(len(bubble) for bubble in bubbles)),
                            "detail": detail,
                            "context_sources": list((context_trace or {}).get("sources") or []),
                            "context_freshness": dict((context_trace or {}).get("freshness") or {}),
                            **(telemetry or {}),
                        },
                    ),
                )
        return AgentBridgeReceipt(
            outcome="answered" if ok else "failed",
            update_id=external_id,
            bubbles=len(bubbles),
            reply_chars=sum(len(bubble) for bubble in bubbles),
        )
