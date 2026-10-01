"""Catch-up after sleep or downtime (M4 task 4.3, ARCHITECTURE.md section 11).

On start, and when the wall clock jumps by more than the loop interval (the computer slept),
the always-on loop:

1. runs the jobs that came due meanwhile. ``JobRunner`` already puts "Late reminder (scheduled
   ...)" on them, so this module reuses it instead of delivering anything itself;
2. never replays more than one run of a recurring check: a daily reminder, brief or scheduled
   task whose next occurrence was also missed is fast-forwarded to its latest missed occurrence,
   so it runs once, not once per missed day. The older runs are skipped, not delivered;
3. lets every connector sync run once (not once per missed interval);
4. tells the user ONCE, with a single outbox message, what was missed. Nothing missed, no message.

This is code only: no model call (section 8.4).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from .audit import AuditEvent, AuditLog
from .brief_schedule import next_daily_occurrence
from .db import Database
from .destinations import destination_from_payload
from .outbox import Outbox

DEFAULT_SLACK_SECONDS = 60.0  # headroom over the loop interval for a slow cycle
UI_DESTINATION = "ui:owner"  # delivered into the "From your companion" conversation (companion_inbox.py)
_BRIEF_KINDS = {"morning_brief", "telegram_morning_brief"}
_REMINDER_KINDS = {"reminder", "telegram_reminder", "nag"}
_TASK_KINDS = {"agent_task"}
_MAX_FAST_FORWARD = 4000

Cause = Literal["wake", "start"]


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


@dataclass
class CatchUpReport:
    cause: Cause
    gap_seconds: float
    late_reminders: int = 0
    late_briefs: int = 0
    late_tasks: int = 0
    stale_skipped: int = 0
    syncs_missed: int = 0
    destinations: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.late_reminders or self.late_briefs or self.late_tasks or self.stale_skipped or self.syncs_missed)

    def message(self) -> str:
        parts: list[str] = []
        if self.late_reminders:
            parts.append(f"{_plural(self.late_reminders, 'reminder')} (delivered late)")
        if self.late_briefs:
            parts.append(f"{_plural(self.late_briefs, 'morning brief')} (delivered late)")
        if self.late_tasks:
            parts.append(f"{_plural(self.late_tasks, 'scheduled task')} (run late)")
        if self.stale_skipped:
            parts.append(f"{_plural(self.stale_skipped, 'older run')} of recurring checks skipped")
        if self.syncs_missed:
            parts.append(f"{_plural(self.syncs_missed, 'sync')} skipped (each ran once to catch up)")
        lead = "While your computer was asleep" if self.cause == "wake" else "While OpenDot was not running"
        return f"{lead}: " + ", ".join(parts) + "."


class CatchUp:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        loop_interval_seconds: float = 5.0,
        slack_seconds: float = DEFAULT_SLACK_SECONDS,
    ) -> None:
        self.database = database
        self._clock = clock
        self.threshold_seconds = loop_interval_seconds + slack_seconds
        self._last_seen: datetime | None = None
        self._started = False

    # -- detection --

    def _last_heartbeat(self) -> datetime | None:
        from .runtime_control import runtime_status

        last = runtime_status(self.database, now=self._clock()).last_cycle_at
        return last.astimezone(UTC) if last is not None else None

    def detect(self) -> tuple[Cause, float] | None:
        """The gap since the loop last ran, when it exceeds the threshold."""
        now = self._clock().astimezone(UTC)
        cause: Cause = "wake"
        if not self._started:
            self._started = True
            cause = "start"
            reference = self._last_heartbeat()
        else:
            reference = self._last_seen
        if reference is None:
            return None
        gap = (now - reference).total_seconds()
        return (cause, gap) if gap > self.threshold_seconds else None

    def mark_cycle_done(self) -> None:
        self._last_seen = self._clock().astimezone(UTC)

    # -- the catch-up pass --

    def prepare(
        self, cause: Cause, gap_seconds: float, connector_intervals: Iterable[float] = ()
    ) -> CatchUpReport:
        """Fast-forward stale recurring jobs and count what the next ``run_due`` will run late."""
        now = self._clock().astimezone(UTC)
        report = CatchUpReport(cause=cause, gap_seconds=gap_seconds)
        destinations: set[str] = set()
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                rows = connection.execute(
                    """
                    SELECT id, kind, schedule_json, next_run_at, payload_json FROM jobs
                    WHERE state = 'active' AND next_run_at IS NOT NULL AND next_run_at <= ?
                    ORDER BY next_run_at ASC, id ASC
                    """,
                    (now.isoformat(),),
                ).fetchall()
                for row in rows:
                    kind = row["kind"]
                    if kind in _REMINDER_KINDS:
                        report.late_reminders += 1
                    elif kind in _BRIEF_KINDS:
                        report.late_briefs += 1
                    elif kind in _TASK_KINDS:
                        report.late_tasks += 1
                    else:
                        continue
                    payload = json.loads(row["payload_json"])
                    try:
                        destinations.add(destination_from_payload(payload))
                    except KeyError:
                        pass
                    schedule = json.loads(row["schedule_json"])
                    recurring = kind in _BRIEF_KINDS or (
                        bool(schedule.get("daily")) and not schedule.get("annual") and kind in {"reminder", "telegram_reminder", "agent_task"}
                    )
                    if not recurring:
                        continue
                    scheduled = datetime.fromisoformat(row["next_run_at"])
                    skipped = 0
                    while skipped < _MAX_FAST_FORWARD:
                        following = next_daily_occurrence(schedule, scheduled)
                        if following > now:
                            break
                        scheduled, skipped = following, skipped + 1
                    if skipped:
                        report.stale_skipped += skipped
                        connection.execute(
                            "UPDATE jobs SET next_run_at = ?, updated_at = ? WHERE id = ? AND state = 'active'",
                            (scheduled.astimezone(UTC).isoformat(), now.isoformat(), row["id"]),
                        )
        report.syncs_missed = sum(1 for interval in connector_intervals if interval <= gap_seconds)
        report.destinations = sorted(destinations)
        return report

    def announce(self, report: CatchUpReport) -> str | None:
        """One outbox message for the whole catch-up; returns its id, or None when nothing was missed."""
        if report.is_empty:
            return None
        now = self._clock().astimezone(UTC)
        destination = report.destinations[0] if report.destinations else UI_DESTINATION
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                message = Outbox.enqueue(
                    connection,
                    destination=destination,
                    payload={"text": report.message(), "kind": "catch_up"},
                    idempotency_key=f"catch-up:{report.cause}:{now.isoformat()}",
                )
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="system:runner",
                        client="catch_up",
                        tool="catch_up_note",
                        outcome="outbox_enqueued",
                        result={
                            "outbox_id": message.id,
                            "gap_seconds": round(report.gap_seconds),
                            "late_reminders": report.late_reminders,
                            "late_briefs": report.late_briefs,
                            "late_tasks": report.late_tasks,
                            "stale_skipped": report.stale_skipped,
                            "syncs_missed": report.syncs_missed,
                        },
                    ),
                )
        return message.id


__all__ = ["CatchUp", "CatchUpReport"]
