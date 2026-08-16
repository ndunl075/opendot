"""Ties the one-shot local commands into one persistent, always-on loop.

Decision 3 calls for "a modular monolith on the always-on PC": one process,
one database. Until now, every capability -- Telegram intake/delivery, due
jobs, connector sync -- was a separate one-shot CLI invocation with no
process actually keeping them running. AlfredRunner is that process: each
cycle handles Telegram and due jobs (the latency-sensitive path), and each
configured connector syncs on its own, independently paced interval.

A missing credential or a failed connector never stops the loop or any
other connector; it is logged to the audit trail and the loop continues.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock, Thread
from typing import Callable, TypeVar

from .audit import AuditEvent, AuditLog
from .db import Database
from .jobs import JobRunner
from .quiet_hours import QuietHours
from .telegram import TelegramPair
from .telegram_actions import TelegramActionWorker
from .telegram_runtime import (
    TelegramLongPoller,
    TelegramOutboxWorker,
    TelegramTransport,
    TelegramTypingHeartbeat,
)
from .slack import SlackOutboxWorker, SlackPair, SlackTransport


T = TypeVar("T")


@dataclass
class ConnectorSync:
    """One named, independently paced sync step."""

    name: str
    interval_seconds: float
    run: Callable[[], None]


@dataclass
class RunOnceReport:
    telegram_polled: bool
    jobs_executed: int
    telegram_delivered: int
    slack_delivered: int
    connectors_synced: list[str]
    agent_replies: int = 0
    memories_learned: int = 0
    actions_executed: int = 0
    errors: list[str] = field(default_factory=list)


class AlfredRunner:
    def __init__(
        self,
        database: Database,
        *,
        telegram_transport: TelegramTransport | None = None,
        telegram_pairs: frozenset[TelegramPair] = frozenset(),
        telegram_chat_ids: frozenset[int] = frozenset(),
        defer_unparsed_to_agent: bool = False,
        agent_bridge: Callable[[], object] | None = None,
        agent_typing_chat_ids: Callable[[], frozenset[int]] | None = None,
        typing_heartbeat_interval_seconds: float = 4.0,
        memory_learning: Callable[[], object] | None = None,
        slack_transport: SlackTransport | None = None,
        slack_pairs: frozenset[SlackPair] = frozenset(),
        slack_channel_ids: frozenset[str] = frozenset(),
        connectors: tuple[ConnectorSync, ...] = (),
        background_connectors: bool = False,
        poll_timeout_seconds: int = 20,
        idle_sleep_seconds: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
        quiet_hours: QuietHours | None = None,
    ) -> None:
        self.database = database
        self.telegram_transport = telegram_transport
        self.telegram_pairs = telegram_pairs
        self.telegram_chat_ids = telegram_chat_ids
        self.defer_unparsed_to_agent = defer_unparsed_to_agent
        self.agent_bridge = agent_bridge
        self.agent_typing_chat_ids = agent_typing_chat_ids
        self.typing_heartbeat_interval_seconds = typing_heartbeat_interval_seconds
        self.memory_learning = memory_learning
        self.slack_transport = slack_transport
        self.slack_pairs = slack_pairs
        self.slack_channel_ids = slack_channel_ids
        self.connectors = connectors
        self.background_connectors = background_connectors
        self.poll_timeout_seconds = poll_timeout_seconds
        self.idle_sleep_seconds = idle_sleep_seconds
        self.sleep = sleep
        self.now = now
        self.quiet_hours = quiet_hours or QuietHours.disabled()
        self._last_synced: dict[str, float] = {}
        self._next_sync_attempt: dict[str, float] = {}
        self._sync_failures: dict[str, int] = {}
        self._connector_thread: Thread | None = None
        self._connector_result: tuple[list[str], list[str]] | None = None
        self._connector_result_lock = Lock()

    def run_forever(
        self, *, iterations: int | None = None, stop_check: Callable[[], bool] = lambda: False
    ) -> None:
        """Loop until interrupted, `iterations` cycles complete, or `stop_check()` returns True.

        `stop_check` lets an external supervisor -- a Windows service's
        SvcStop handler, for instance -- request a clean stop between
        cycles without this module needing to import any platform-specific
        signaling primitive itself. It defaults to never stopping, so every
        existing caller (including `alfred run`'s own KeyboardInterrupt
        handling) is unaffected.
        """
        count = 0
        while (iterations is None or count < iterations) and not stop_check():
            report = self.run_once()
            count += 1
            if (iterations is None or count < iterations) and not stop_check():
                poll_failed = any(error.startswith("telegram_poll:") for error in report.errors)
                # A failed long poll has already spent its timeout waiting.
                # Retry quickly, but retain a one-second floor so a provider
                # outage cannot turn into a tight request loop.
                delay = min(self.idle_sleep_seconds, 1.0) if poll_failed else self.idle_sleep_seconds
                self.sleep(delay)

    def run_once(self) -> RunOnceReport:
        errors: list[str] = []
        transport = self.telegram_transport

        telegram_polled = False
        if transport is not None and self.telegram_pairs:
            pairs = set(self.telegram_pairs)
            polled, _ = self._safe(
                "telegram_poll",
                lambda: TelegramLongPoller(
                    self.database,
                    transport,
                    pairs,
                    defer_unparsed_to_agent=self.defer_unparsed_to_agent,
                ).poll_once(timeout_seconds=self.poll_timeout_seconds),
                errors,
            )
            telegram_polled = polled

        actions_executed = 0
        if transport is not None and self.telegram_pairs:
            actions_ok, action_count = self._safe(
                "telegram_actions",
                lambda: TelegramActionWorker(self.database).run_pending(),
                errors,
            )
            if actions_ok and action_count is not None:
                actions_executed = int(action_count)

        ran, due_jobs = self._safe("run_due", lambda: JobRunner(self.database).run_due(), errors)
        jobs_executed = len(due_jobs) if ran and due_jobs is not None else 0

        # Flushed here, before the agent runs, so the acknowledgement actually
        # lands while the answer is still being written. Delivering once at
        # the end of the cycle instead would push both out in the same
        # instant, which makes the acknowledgement pointless and lets the
        # answer overtake it (rows created in the same second have no
        # guaranteed order).
        telegram_delivered = self._deliver_telegram(transport, errors)

        # Between intake and the final delivery, not in the connector list:
        # someone is waiting on this reply, and connectors run after delivery,
        # which stranded every answer in the outbox for an extra cycle.
        agent_replies = 0
        if self.agent_bridge is not None:
            typing_chat_ids: frozenset[int] = frozenset()
            if transport is not None and self.agent_typing_chat_ids is not None:
                try:
                    typing_chat_ids = self.agent_typing_chat_ids()
                except Exception:
                    pass
            with TelegramTypingHeartbeat(
                transport,
                typing_chat_ids,
                interval_seconds=self.typing_heartbeat_interval_seconds,
            ):
                answered, result = self._safe("hermes_bridge", self.agent_bridge, errors)
            if answered and result is not None:
                agent_replies = int(getattr(result, "answered", 0))
            telegram_delivered += self._deliver_telegram(transport, errors)

        # Learning happens only after the user-facing answer is delivered.
        # The default rules extractor is local and fast; model-backed
        # extractors can use this same hook without delaying the reply itself.
        memories_learned = 0
        if self.memory_learning is not None:
            learned_ok, learned = self._safe("memory_learning", self.memory_learning, errors)
            if learned_ok and learned is not None:
                memories_learned = int(getattr(learned, "promoted", 0))

        slack_delivered = 0
        if self.slack_transport is not None and self.slack_channel_ids:
            channel_ids = set(self.slack_channel_ids)
            delivered_ok, delivered = self._safe(
                "slack_deliver",
                lambda: SlackOutboxWorker(
                    self.database,
                    self.slack_transport,
                    channel_ids,
                    quiet_hours=self.quiet_hours,
                ).deliver_pending(),
                errors,
            )
            slack_delivered = len(delivered) if delivered_ok and delivered is not None else 0

        if self.background_connectors:
            synced, connector_errors = self._advance_connector_worker()
            errors.extend(connector_errors)
        else:
            synced = self._sync_connectors(self.connectors, errors)

        return RunOnceReport(
            telegram_polled=telegram_polled,
            jobs_executed=jobs_executed,
            telegram_delivered=telegram_delivered,
            slack_delivered=slack_delivered,
            connectors_synced=synced,
            agent_replies=agent_replies,
            memories_learned=memories_learned,
            actions_executed=actions_executed,
            errors=errors,
        )

    def _sync_connectors(
        self, connectors: tuple[ConnectorSync, ...], errors: list[str]
    ) -> list[str]:
        """Run a connector batch sequentially and apply its pacing state."""
        synced: list[str] = []
        for connector in connectors:
            if not self._due(connector):
                continue
            ok, _ = self._safe(f"connector_sync:{connector.name}", connector.run, errors)
            if ok:
                self._last_synced[connector.name] = self.now()
                self._next_sync_attempt.pop(connector.name, None)
                self._sync_failures.pop(connector.name, None)
                synced.append(connector.name)
            else:
                failures = self._sync_failures.get(connector.name, 0) + 1
                self._sync_failures[connector.name] = failures
                # Retry quickly once, then back off to at most the connector's
                # normal interval. This prevents a dead provider from being
                # hammered every five-second runner cycle.
                delay = min(connector.interval_seconds, 30.0 * (2 ** (failures - 1)))
                self._next_sync_attempt[connector.name] = self.now() + delay
        return synced

    def _advance_connector_worker(self) -> tuple[list[str], list[str]]:
        """Collect one finished batch and start the next without blocking chat.

        There is exactly one daemon worker and connectors remain sequential.
        The only concurrency is between that bounded batch and the
        latency-sensitive intake/answer/delivery path.
        """
        completed: tuple[list[str], list[str]] = ([], [])
        thread = self._connector_thread
        if thread is not None:
            if thread.is_alive():
                return completed
            thread.join()
            with self._connector_result_lock:
                if self._connector_result is not None:
                    completed = self._connector_result
                self._connector_result = None
            self._connector_thread = None

        due = tuple(connector for connector in self.connectors if self._due(connector))
        if due:
            worker = Thread(
                target=self._run_connector_worker,
                args=(due,),
                name="alfred-connectors",
                daemon=True,
            )
            self._connector_thread = worker
            worker.start()
        return completed

    def _run_connector_worker(self, connectors: tuple[ConnectorSync, ...]) -> None:
        errors: list[str] = []
        synced: list[str] = []
        try:
            synced = self._sync_connectors(connectors, errors)
        except Exception as error:  # pragma: no cover - last-resort thread containment
            errors.append(f"connector_worker: {error.__class__.__name__}: {error}")
        with self._connector_result_lock:
            self._connector_result = (synced, errors)

    def _deliver_telegram(self, transport: TelegramTransport | None, errors: list[str]) -> int:
        """Flush the Telegram outbox once; a no-op when Telegram isn't configured."""
        if transport is None or not self.telegram_chat_ids:
            return 0
        chat_ids = set(self.telegram_chat_ids)
        delivered_ok, delivered = self._safe(
            "telegram_deliver",
            lambda: TelegramOutboxWorker(
                self.database,
                transport,
                chat_ids,
                quiet_hours=self.quiet_hours,
            ).deliver_pending(),
            errors,
        )
        return len(delivered) if delivered_ok and delivered is not None else 0

    def _due(self, connector: ConnectorSync) -> bool:
        next_attempt = self._next_sync_attempt.get(connector.name)
        if next_attempt is not None and self.now() < next_attempt:
            return False
        last = self._last_synced.get(connector.name)
        return last is None or (self.now() - last) >= connector.interval_seconds

    def _safe(self, context: str, action: Callable[[], T], errors: list[str]) -> tuple[bool, T | None]:
        """Run ``action``; on failure, audit and log it but never raise into the loop."""
        try:
            return True, action()
        except Exception as error:
            reason = f"{error.__class__.__name__}: {error}"
            errors.append(f"{context}: {reason}")
            AuditLog(self.database).append(
                AuditEvent(actor="system:runner", client="runner", tool=context, outcome="error", result={"error": reason})
            )
            print(f"[alfred run] {context} failed: {reason}")
            return False, None
