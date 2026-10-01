"""Weekly review routine: a plain-code summary of the week, delivered Sunday evening by default.

Tasks completed and overdue, reminders, calendar load, pull requests and plan credits used. All of it
comes from local tables (and the optional GitHub PR report), so a quiet week costs no model call. The
opt-in friendlier wording is one cheap/low ``summarize`` pass with the same fallback rules as the brief.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ..usage.meter import UsageMeter
from .base import Outcome, Routine, RoutineContext, RoutineState
from .morning_brief import reword
from .settings import WEEKLY_REVIEW_KEY, WeeklyReviewSettings

WINDOW = timedelta(days=7)
LIST_LIMIT = 8
_REMINDER_KINDS = ("reminder", "telegram_reminder")


@dataclass
class WeekSummary:
    now: datetime
    completed: list[str] = field(default_factory=list)
    overdue: list[str] = field(default_factory=list)
    reminders_sent: int = 0
    reminders_ahead: list[str] = field(default_factory=list)
    events_past: int = 0
    hours_past: float = 0.0
    events_ahead: int = 0
    hours_ahead: float = 0.0
    pull_requests: list[str] = field(default_factory=list)
    pull_request_note: str = ""
    credits: float = 0.0
    credits_by_job: list[tuple[str, float]] = field(default_factory=list)

    def has_content(self) -> bool:
        return bool(
            self.completed
            or self.overdue
            or self.reminders_sent
            or self.reminders_ahead
            or self.events_past
            or self.events_ahead
            or self.pull_requests
            or self.credits
        )

    def render(self) -> str:
        lines = [f"Weekly review — week ending {self.now.date().isoformat()}"]

        def section(title: str, items: list[str]) -> None:
            if not items:
                return
            lines.append(f"\n{title}:")
            lines.extend(f"- {item}" for item in items[:LIST_LIMIT])
            if len(items) > LIST_LIMIT:
                lines.append(f"- ...and {len(items) - LIST_LIMIT} more")

        section(f"Tasks completed ({len(self.completed)})", self.completed)
        section(f"Overdue now ({len(self.overdue)})", self.overdue)
        if self.reminders_sent or self.reminders_ahead:
            lines.append(f"\nReminders: {self.reminders_sent} delivered this week.")
            section("Coming up this week", self.reminders_ahead)
        if self.events_past or self.events_ahead:
            lines.append(
                f"\nCalendar: {self.events_past} event(s) last week ({self.hours_past:.1f} h scheduled); "
                f"{self.events_ahead} ahead ({self.hours_ahead:.1f} h)."
            )
        if self.pull_request_note:
            lines.append(f"\nPull requests: {self.pull_request_note}")
        section("Open pull requests", self.pull_requests)
        if self.credits:
            top = ", ".join(f"{job} {credits:.2f}" for job, credits in self.credits_by_job[:3])
            lines.append(f"\nPlan credits used this week: {self.credits:.2f}" + (f" ({top})." if top else "."))
        if not self.has_content():
            lines.append("\nA quiet week: nothing to report.")
        return "\n".join(lines)


def _aware(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def summarize_week(context: RoutineContext, now: datetime) -> WeekSummary:
    summary = WeekSummary(now=now)
    since = now - WINDOW
    ahead = now + WINDOW
    database = context.database
    with database.connect() as connection:
        for row in connection.execute(
            "SELECT title FROM tasks WHERE state = 'completed' AND updated_at >= ? AND updated_at <= ? ORDER BY updated_at",
            (since.isoformat(), now.isoformat()),
        ).fetchall():
            summary.completed.append(str(row["title"]))
        for row in connection.execute(
            "SELECT title, due_at FROM tasks WHERE state = 'open' AND due_at IS NOT NULL ORDER BY due_at"
        ).fetchall():
            due = _aware(row["due_at"])
            if due is not None and due < now:
                summary.overdue.append(f"{row['title']} (due {due.date().isoformat()})")
        marks = ",".join("?" for _ in _REMINDER_KINDS)
        summary.reminders_sent = int(
            connection.execute(
                f"SELECT COUNT(*) FROM jobs WHERE kind IN ({marks}) AND state = 'completed' "
                "AND updated_at >= ? AND updated_at <= ?",
                (*_REMINDER_KINDS, since.isoformat(), now.isoformat()),
            ).fetchone()[0]
        )
        for row in connection.execute(
            f"SELECT payload_json, next_run_at FROM jobs WHERE kind IN ({marks}) AND state = 'active' "
            "AND next_run_at IS NOT NULL ORDER BY next_run_at",
            _REMINDER_KINDS,
        ).fetchall():
            due = _aware(row["next_run_at"])
            if due is not None and now <= due <= ahead:
                text = str(json.loads(row["payload_json"]).get("text", "")).strip()
                summary.reminders_ahead.append(f"{text} ({due.date().isoformat()})")
        for row in connection.execute(
            "SELECT payload_json FROM connector_records "
            "WHERE connector = 'google_calendar' AND record_type = 'event' AND active = 1"
        ).fetchall():
            payload = json.loads(row["payload_json"])
            start, end = _aware(payload.get("start")), _aware(payload.get("end"))
            if start is None:
                continue
            hours = 0.0
            if end is not None and timedelta(0) < end - start < timedelta(hours=12):
                hours = (end - start).total_seconds() / 3600
            if since <= start < now:
                summary.events_past += 1
                summary.hours_past += hours
            elif now <= start < ahead:
                summary.events_ahead += 1
                summary.hours_ahead += hours
    if context.pull_requests is not None:
        try:
            report = context.pull_requests()
        except Exception as error:  # GitHub being down must not cost the user their review
            summary.pull_request_note = f"unavailable ({type(error).__name__})"
        else:
            items = list(report.pull_requests)
            stale = sum(1 for item in items if item.stale)
            authored = sum(1 for item in items if item.role == "authored")
            summary.pull_request_note = (
                f"{len(items)} open ({authored} yours, {len(items) - authored} awaiting your review"
                + (f", {stale} stale" if stale else "")
                + ")."
            )
            summary.pull_requests = [
                f"{item.title} ({item.repo})" + (" [stale]" if item.stale else "")
                for item in items
                if item.stale or item.role == "review_requested"
            ]
    usage = UsageMeter(database, clock=lambda: now).summary(days=7)
    summary.credits = usage.total_credits
    summary.credits_by_job = [(line.key, line.credits) for line in usage.by_job_type]
    return summary


class WeeklyReviewRoutine(Routine):
    name = "weekly_review"
    settings_key = WEEKLY_REVIEW_KEY
    settings_cls = WeeklyReviewSettings
    kind = "weekly"

    def run(
        self,
        context: RoutineContext,
        settings: Any,
        state: RoutineState,
        now: datetime,
        scheduled_at: datetime | None,
    ) -> Outcome:
        summary = summarize_week(context, now)
        plain = summary.render()
        if not settings.friendlier_wording or not summary.has_content():
            return Outcome(status="delivered", text=plain)
        _, outcome = reword(context, plain)
        return outcome
