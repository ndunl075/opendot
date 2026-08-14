"""Deterministic local task briefing; connectors add their records later."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .db import Database
from .models import TextGenerationProvider


class BriefItem(BaseModel):
    title: str
    due_at: datetime | None
    source: str = "Alfred task"
    url: str | None = None
    end_at: datetime | None = None


class MorningBrief(BaseModel):
    generated_at: datetime
    overdue: list[BriefItem]
    due_today: list[BriefItem]
    upcoming: list[BriefItem]
    no_due_date: list[BriefItem]
    missing_assignments: list[BriefItem] = []
    calendar_today: list[BriefItem] = []
    github_notifications: list[BriefItem] = []
    scheduled_at: datetime | None = None
    conflicts: list[str] = []
    source_freshness: dict[str, str] = {}

    def render(self) -> str:
        """Render a concise, source-explicit local brief without a model call."""
        lines = [
            f"Morning brief — {self.generated_at.date().isoformat()}",
            f"Freshness: local Alfred tasks checked {self.generated_at.isoformat()}.",
        ]
        for source, checked_at in sorted(self.source_freshness.items()):
            lines.append(f"Freshness: {source} checked {checked_at}.")
        if self.scheduled_at is not None:
            lines.append(f"Note: delivered late (scheduled {self.scheduled_at.isoformat()}, sent after a missed run).")
        empty_length = len(lines)
        for heading, items in (
            ("Overdue", self.overdue),
            ("Due today", self.due_today),
            ("Next 7 days", self.upcoming),
            ("Canvas missing", self.missing_assignments),
            ("Today's calendar", self.calendar_today),
            ("GitHub notifications", self.github_notifications),
            ("Calendar conflicts", [BriefItem(title=value, due_at=None) for value in self.conflicts]),
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
    def __init__(self, database: Database, *, llm_writer: TextGenerationProvider | None = None) -> None:
        """``llm_writer`` is opt-in; without one, write_brief() is just render()."""
        self.database = database
        self.llm_writer = llm_writer

    def morning_brief(
        self,
        now: datetime | None = None,
        *,
        scheduled_at: datetime | None = None,
        timezone_name: str = "UTC",
    ) -> MorningBrief:
        """Rank locally owned open tasks by deadline without any LLM dependency.

        ``scheduled_at`` is set only when this run is a late/missed-run recovery,
        so the rendered brief can disclose the delay to the reader.
        """
        timezone = ZoneInfo(timezone_name)
        generated_at = (now or datetime.now(UTC)).astimezone(timezone)
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
                WHERE connector = 'google_calendar' AND record_type = 'event' AND active = 1
                """
            ).fetchall()
            github_rows = connection.execute(
                """
                SELECT payload_json FROM connector_records
                WHERE connector = 'github' AND account = 'self' AND record_type = 'notification' AND active = 1
                """
            ).fetchall()
            freshness_rows = connection.execute(
                """
                SELECT connector, MAX(last_success_at) AS last_success_at
                FROM sync_state
                WHERE connector IN ('canvas', 'google_calendar', 'github', 'google_health')
                  AND last_success_at IS NOT NULL
                GROUP BY connector
                """
            ).fetchall()
        brief = MorningBrief(
            generated_at=generated_at,
            scheduled_at=scheduled_at.astimezone(timezone) if scheduled_at else None,
            overdue=[], due_today=[], upcoming=[], no_due_date=[],
            source_freshness={str(row["connector"]): str(row["last_success_at"]) for row in freshness_rows},
        )
        end_of_window = generated_at.date() + timedelta(days=7)
        for row in rows:
            due_at = datetime.fromisoformat(row["due_at"]).astimezone(timezone) if row["due_at"] else None
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
            if due_at:
                due_at = due_at.astimezone(timezone)
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
            end = _parse_optional_timestamp(payload.get("end"))
            if start:
                start = start.astimezone(timezone)
            if end:
                end = end.astimezone(timezone)
            if start and start.date() == generated_at.date():
                brief.calendar_today.append(
                    BriefItem(
                        title=payload.get("title", "Untitled calendar event"),
                        due_at=start,
                        source="Google Calendar",
                        url=payload.get("html_url"),
                        end_at=end,
                    )
                )
        for row in github_rows:
            payload = json.loads(row["payload_json"])
            repo = payload.get("repo")
            title = payload.get("title", "Untitled GitHub notification")
            brief.github_notifications.append(
                BriefItem(
                    title=f"{repo}: {title}" if repo else title,
                    due_at=None,
                    source="GitHub",
                    url=payload.get("html_url"),
                )
            )
        for collection in (
            brief.overdue,
            brief.due_today,
            brief.upcoming,
            brief.missing_assignments,
            brief.calendar_today,
            brief.github_notifications,
        ):
            collection.sort(key=lambda item: (item.due_at is None, item.due_at or generated_at, item.title))
        timed = [item for item in brief.calendar_today if item.due_at and item.end_at]
        for index, first in enumerate(timed):
            for second in timed[index + 1:]:
                if first.due_at < second.end_at and second.due_at < first.end_at:
                    brief.conflicts.append(f"{first.title} overlaps {second.title}")
        return brief

    def write_brief(self, brief: MorningBrief, *, actor: str = "system:briefing") -> str:
        """Render the brief, optionally asking the local model to write it up.

        Every fact still comes from the deterministic render passed to the
        model, never from the model's own knowledge -- only the wording
        changes. Falls back to the deterministic render on any model
        failure (e.g. Ollama isn't running); a missing local model must
        never cost the user their brief.
        """
        deterministic_text = brief.render()
        if self.llm_writer is None:
            return deterministic_text
        prompt = (
            "Rewrite the following task briefing in a short, warm, plain-text "
            "message. Preserve every fact, date, and link exactly as given. Do "
            "not add or invent anything not present below.\n\n" + deterministic_text
        )
        try:
            result = self.llm_writer.generate(prompt)
        except Exception as error:
            self._audit_llm_pass(actor, outcome="error", result={"error": f"{error.__class__.__name__}: {error}"})
            return deterministic_text
        self._audit_llm_pass(
            actor,
            outcome="ok",
            result={
                "model": result.model,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "estimated_cost_usd": 0.0,
            },
        )
        return result.text

    def _audit_llm_pass(self, actor: str, *, outcome: str, result: dict) -> None:
        AuditLog(self.database).append(
            AuditEvent(actor=actor, client="briefing", tool="brief_llm_pass", outcome=outcome, result=result)
        )


def _parse_optional_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
