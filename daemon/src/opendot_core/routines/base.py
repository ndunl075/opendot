"""Shared types and schedule arithmetic for the built-in routines."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from ..db import Database
from .model_pass import ModelPass
from .settings import parse_time

MAX_SEEN_IDS = 1000


class RoutineState(BaseModel):
    """What a routine remembers between runs. Lives in the routine's ``jobs`` row (``payload_json``)."""

    since: datetime | None = None
    """When the routine was first set up. A scheduled time before this is never made up afterwards."""
    last_run_at: datetime | None = None
    seen_ids: list[str] = Field(default_factory=list)
    """Inbox triage only: unread message ids that were already triaged and are still unread."""

    def baseline(self) -> datetime | None:
        """The later of the last run and the moment the routine was switched on."""
        marks = [mark for mark in (self.last_run_at, self.since) if mark is not None]
        return max(marks) if marks else None


@dataclass
class RoutineContext:
    database: Database
    model: ModelPass
    pull_requests: Callable[[], Any] | None = None
    """Optional: returns a ``PullRequestReport`` (needs GitHub). Without it the weekly review omits PRs."""


@dataclass
class Outcome:
    status: str
    """``delivered`` or ``nothing_new`` (a ``skipped_*`` status is set by the scheduler, not by a routine)."""
    text: str | None = None
    model_called: bool = False
    used_model: bool = False
    fallback_reason: str = ""
    credits: float = 0.0
    seen_ids: list[str] | None = None
    dedupe: str = ""
    """Extra text for the outbox idempotency key, so a retry of the same work never delivers twice."""
    details: dict[str, Any] = field(default_factory=dict)


class Routine:
    name = ""
    settings_key = ""
    settings_cls: type[BaseModel]
    kind = "daily"
    """``daily``, ``weekly`` or ``interval``."""

    def run(
        self,
        context: RoutineContext,
        settings: Any,
        state: RoutineState,
        now: datetime,
        scheduled_at: datetime | None,
    ) -> Outcome:
        raise NotImplementedError

    # -- schedule ----------------------------------------------------------------

    def latest_occurrence(self, settings: Any, now: datetime) -> datetime | None:
        """The most recent scheduled instant at or before ``now`` (UTC), for daily and weekly routines."""
        if self.kind == "interval":
            return None
        zone = ZoneInfo(settings.timezone)
        local_now = now.astimezone(zone)
        at = parse_time(settings.time)
        candidate = datetime.combine(local_now.date(), at, tzinfo=zone)
        if self.kind == "weekly":
            candidate -= timedelta(days=(local_now.weekday() - settings.weekday) % 7)
        elif candidate > local_now:
            candidate -= timedelta(days=1)
        if candidate > local_now:
            candidate -= timedelta(days=7)
        return candidate.astimezone(UTC)

    def next_occurrence(self, settings: Any, now: datetime, state: RoutineState) -> datetime | None:
        if self.kind == "interval":
            base = state.baseline() or now
            return base + timedelta(minutes=settings.check_every_minutes)
        latest = self.latest_occurrence(settings, now)
        assert latest is not None
        step = timedelta(days=7 if self.kind == "weekly" else 1)
        # Wall-clock arithmetic in the user's zone, so a daylight-saving change keeps the same local time.
        zone = ZoneInfo(settings.timezone)
        local = latest.astimezone(zone).replace(tzinfo=None) + step
        return local.replace(tzinfo=zone).astimezone(UTC)

    def due(self, settings: Any, state: RoutineState, now: datetime) -> tuple[bool, datetime | None]:
        """Whether a run is owed at ``now`` and the scheduled instant it is for."""
        if self.kind == "interval":
            last = state.baseline()
            if last is None:
                return True, now
            return now - last >= timedelta(minutes=settings.check_every_minutes), now
        occurrence = self.latest_occurrence(settings, now)
        assert occurrence is not None
        baseline = state.baseline()
        return (baseline is None or occurrence > baseline), occurrence
