"""Deterministic local task briefing; connectors add their records later."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from .db import Database


class BriefItem(BaseModel):
    title: str
    due_at: datetime | None
    source: str = "Alfred task"
    url: str | None = None


class MorningBrief(BaseModel):
    generated_at: datetime
    overdue: list[BriefItem]
    due_today: list[BriefItem]
    upcoming: list[BriefItem]
    no_due_date: list[BriefItem]
    missing_assignments: list[BriefItem] = []
    calendar_today: list[BriefItem] = []
    scheduled_at: datetime | None = None

    def render(self) -> str:
        """Render a concise, source-explicit local brief without a model call."""
        lines = [
            f"Morning brief — {self.generated_at.date().isoformat()}",
            f"Freshness: local Alfred tasks checked {self.generated_at.isoformat()}.",
        ]
        if self.scheduled_at is not None:
            lines.append(f"Note: delivered late (scheduled {self.scheduled_at.isoformat()}, sent after a missed run).")
        empty_length = len(lines)
        for heading, items in (
            ("Overdue", self.overdue),
            ("Due today", self.due_today),
            ("Next 7 days", self.upcoming),
            ("Canvas missing", self.missing_assignments),
            ("Today's calendar", self.calendar_today),
            ("No due date", self.no_due_date),
        ):
            if not items:
                continue
            lines.append(f"\n{heading}:")
            for item in items:
                suffix = f" ({item.due_at.isoformat()})" if item.due_at else ""
                if item.source != "Alfred task":
                    suffix += f" — {item.source}"
                if item.url:
                    suffix += f" <{item.url}>"
                lines.append(f"- {item.title}{suffix}")
        if len(lines) == empty_length:
            lines.append("\nNo open tasks yet.")
        return "\n".join(lines)


class BriefingService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def morning_brief(self, now: datetime | None = None, *, scheduled_at: datetime | None = None) -> MorningBrief:
        """Rank locally owned open tasks by deadline without any LLM dependency.

        ``scheduled_at`` is set only when this run is a late/missed-run recovery,
        so the rendered brief can disclose the delay to the reader.
        """
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        self.database.migrate()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT title, due_at FROM tasks WHERE state = 'open' ORDER BY due_at IS NULL, due_at, created_at"
            ).fetchall()
            canvas_rows = connection.execute(
                """
                SELECT record_type, payload_json FROM connector_records
                WHERE connector = 'canvas' AND account = 'self' AND active = 1
                """
            ).fetchall()
            calendar_rows = connection.execute(
                """
                SELECT payload_json FROM connector_records
                WHERE connector = 'google_calendar' AND account = 'primary' AND record_type = 'event' AND active = 1
                """
            ).fetchall()
        brief = MorningBrief(
            generated_at=generated_at, scheduled_at=scheduled_at, overdue=[], due_today=[], upcoming=[], no_due_date=[]
        )
        end_of_window = generated_at.date() + timedelta(days=7)
        for row in rows:
            due_at = datetime.fromisoformat(row["due_at"]) if row["due_at"] else None
            item = BriefItem(title=row["title"], due_at=due_at)
            if due_at is None:
                brief.no_due_date.append(item)
            elif due_at < generated_at:
                brief.overdue.append(item)
            elif due_at.date() == generated_at.date():
                brief.due_today.append(item)
            elif due_at.date() <= end_of_window:
                brief.upcoming.append(item)
        for row in canvas_rows:
            payload = json.loads(row["payload_json"])
            due_at = _parse_optional_timestamp(payload.get("due_at"))
            item = BriefItem(
                title=payload.get("title", "Untitled Canvas assignment"),
                due_at=due_at,
                source="Canvas",
                url=payload.get("html_url"),
            )
            if row["record_type"] == "missing":
                brief.missing_assignments.append(item)
            elif due_at is None:
                brief.no_due_date.append(item)
            elif due_at < generated_at:
                brief.overdue.append(item)
            elif due_at.date() == generated_at.date():
                brief.due_today.append(item)
            elif due_at.date() <= end_of_window:
                brief.upcoming.append(item)
        for row in calendar_rows:
            payload = json.loads(row["payload_json"])
            start = _parse_optional_timestamp(payload.get("start"))
            if start and start.date() == generated_at.date():
                brief.calendar_today.append(
                    BriefItem(
                        title=payload.get("title", "Untitled calendar event"),
                        due_at=start,
                        source="Google Calendar",
                        url=payload.get("html_url"),
                    )
                )
        for collection in (brief.overdue, brief.due_today, brief.upcoming, brief.missing_assignments, brief.calendar_today):
            collection.sort(key=lambda item: (item.due_at is None, item.due_at or generated_at, item.title))
        return brief


def _parse_optional_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
