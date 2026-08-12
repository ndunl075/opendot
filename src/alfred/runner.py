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
    errors: list[str] = field(default_factory=list)


class AlfredRunner:
    def __init__(
        self,
        database: Database,
        *,
        telegram_transport: TelegramTransport | None = None,
        telegram_pairs: frozenset[TelegramPair] = frozenset(),
        telegram_chat_ids: frozenset[int] = frozenset(),
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
        self.slack_transport = slack_transport
        self.slack_pairs = slack_pairs
        self.slack_channel_ids = slack_channel_ids
        self.connectors = connectors
        self.poll_timeout_seconds = poll_timeout_seconds
        self.idle_sleep_seconds = idle_sleep_seconds
        self.sleep = sleep
        self.now = now
        self._last_synced: dict[str, float] = {}

    def run_forever(self, *, iterations: int | None = None) -> None:
        """Loop until interrupted, or for a fixed number of cycles when testing."""
        count = 0
        while iterations is None or count < iterations:
            self.run_once()
            count += 1
            if iterations is None or count < iterations:
                self.sleep(self.idle_sleep_seconds)

    def run_once(self) -> RunOnceReport:
        errors: list[str] = []
        transport = self.telegram_transport

        telegram_polled = False
        if transport is not None and self.telegram_pairs:
            pairs = set(self.telegram_pairs)
            polled, _ = self._safe(
                "telegram_poll",
                lambda: TelegramLongPoller(self.database, transport, pairs).poll_once(
                    timeout_seconds=self.poll_timeout_seconds
                ),
                errors,
            )
            telegram_polled = polled

        ran, due_jobs = self._safe("run_due", lambda: JobRunner(self.database).run_due(), errors)
        jobs_executed = len(due_jobs) if ran and due_jobs is not None else 0

        telegram_delivered = 0
        if transport is not None and self.telegram_chat_ids:
            chat_ids = set(self.telegram_chat_ids)
            delivered_ok, delivered = self._safe(
                "telegram_deliver",
                lambda: TelegramOutboxWorker(self.database, transport, chat_ids).deliver_pending(),
                errors,
            )
            telegram_delivered = len(delivered) if delivered_ok and delivered is not None else 0

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
            errors=errors,
        )

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
