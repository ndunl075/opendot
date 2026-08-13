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
from typing import Callable, TypeVar

from .audit import AuditEvent, AuditLog
from .db import Database
from .jobs import JobRunner
from .telegram import TelegramPair
from .telegram_runtime import TelegramLongPoller, TelegramOutboxWorker, TelegramTransport
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
        slack_transport: SlackTransport | None = None,
        slack_pairs: frozenset[SlackPair] = frozenset(),
        slack_channel_ids: frozenset[str] = frozenset(),
        connectors: tuple[ConnectorSync, ...] = (),
        poll_timeout_seconds: int = 20,
        idle_sleep_seconds: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.database = database
        self.telegram_transport = telegram_transport
        self.telegram_pairs = telegram_pairs
        self.telegram_chat_ids = telegram_chat_ids
        self.defer_unparsed_to_agent = defer_unparsed_to_agent
        self.agent_bridge = agent_bridge
        self.slack_transport = slack_transport
        self.slack_pairs = slack_pairs
        self.slack_channel_ids = slack_channel_ids
        self.connectors = connectors
        self.poll_timeout_seconds = poll_timeout_seconds
        self.idle_sleep_seconds = idle_sleep_seconds
        self.sleep = sleep
        self.now = now
        self._last_synced: dict[str, float] = {}

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
            self.run_once()
            count += 1
            if (iterations is None or count < iterations) and not stop_check():
                self.sleep(self.idle_sleep_seconds)

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
            answered, result = self._safe("hermes_bridge", self.agent_bridge, errors)
            if answered and result is not None:
                agent_replies = int(getattr(result, "answered", 0))
            telegram_delivered += self._deliver_telegram(transport, errors)

        slack_delivered = 0
        if self.slack_transport is not None and self.slack_channel_ids:
            channel_ids = set(self.slack_channel_ids)
            delivered_ok, delivered = self._safe(
                "slack_deliver",
                lambda: SlackOutboxWorker(self.database, self.slack_transport, channel_ids).deliver_pending(),
                errors,
            )
            slack_delivered = len(delivered) if delivered_ok and delivered is not None else 0

        synced: list[str] = []
        for connector in self.connectors:
            if not self._due(connector):
                continue
            ok, _ = self._safe(f"connector_sync:{connector.name}", connector.run, errors)
            if ok:
                self._last_synced[connector.name] = self.now()
                synced.append(connector.name)

        return RunOnceReport(
            telegram_polled=telegram_polled,
            jobs_executed=jobs_executed,
            telegram_delivered=telegram_delivered,
            slack_delivered=slack_delivered,
            connectors_synced=synced,
            agent_replies=agent_replies,
            errors=errors,
        )

    def _deliver_telegram(self, transport: TelegramTransport | None, errors: list[str]) -> int:
        """Flush the Telegram outbox once; a no-op when Telegram isn't configured."""
        if transport is None or not self.telegram_chat_ids:
            return 0
        chat_ids = set(self.telegram_chat_ids)
        delivered_ok, delivered = self._safe(
            "telegram_deliver",
            lambda: TelegramOutboxWorker(self.database, transport, chat_ids).deliver_pending(),
            errors,
        )
        return len(delivered) if delivered_ok and delivered is not None else 0

    def _due(self, connector: ConnectorSync) -> bool:
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
