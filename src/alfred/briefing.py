"""Deterministic local task briefing; connectors add their records later."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from .db import Database


class BriefItem(BaseModel):
    title: str
    due_at: datetime | None


class MorningBrief(BaseModel):
    generated_at: datetime
    overdue: list[BriefItem]
    due_today: list[BriefItem]
    upcoming: list[BriefItem]
    no_due_date: list[BriefItem]

    def render(self) -> str:
        """Render a concise, source-explicit local brief without a model call."""
        lines = [
            f"Morning brief — {self.generated_at.date().isoformat()}",
            f"Freshness: local Alfred tasks checked {self.generated_at.isoformat()}.",
        ]
        for heading, items in (
            ("Overdue", self.overdue),
            ("Due today", self.due_today),
            ("Next 7 days", self.upcoming),
            ("No due date", self.no_due_date),
        ):
            if not items:
                continue
            lines.append(f"\n{heading}:")
            lines.extend(
                f"- {item.title}" + (f" ({item.due_at.isoformat()})" if item.due_at else "")
                for item in items
            )
        if len(lines) == 2:
            lines.append("\nNo open tasks yet.")
        return "\n".join(lines)


class BriefingService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def morning_brief(self, now: datetime | None = None) -> MorningBrief:
        """Rank locally owned open tasks by deadline without any LLM dependency."""
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        self.database.migrate()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT title, due_at FROM tasks WHERE state = 'open' ORDER BY due_at IS NULL, due_at, created_at"
            ).fetchall()
        brief = MorningBrief(generated_at=generated_at, overdue=[], due_today=[], upcoming=[], no_due_date=[])
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
        return brief
