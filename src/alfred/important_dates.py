"""Annual birthdays and important dates over tasks + reminder jobs.

These are not a second calendar. Each date is an open task whose ``due_at`` is
the next occurrence, plus an annual reminder job that delivers on that day and
rolls both the job and the task forward one year. The morning brief / weekly
window already ranks open tasks by due date; a dedicated brief section names
the ones that are birthdays or anniversaries so they do not look like homework.
"""

from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from .db import Database
from .events import EventStore
from .tasks import TaskStore

DateKind = Literal["birthday", "anniversary", "other"]


class ImportantDate(BaseModel):
    job_id: str
    task_id: str
    label: str
    kind: DateKind
    month: int
    day: int
    year: int | None = None
    next_at: datetime
    timezone: str
    #: Age the person turns on ``next_at`` when ``year`` is known; None otherwise.
    turns: int | None = None


def clamp_month_day(year: int, month: int, day: int) -> int:
    """Return a valid day-of-month, folding Feb 29 onto Feb 28 in common years."""
    return min(day, calendar.monthrange(year, month)[1])


def next_annual_occurrence(
    month: int,
    day: int,
    after: datetime,
    *,
    timezone_name: str,
    local_time: time | None = None,
) -> datetime:
    """First annual wall-clock occurrence strictly after ``after``.

    Uses the local calendar date in ``timezone_name``. Feb 29 lands on Feb 28
    in non-leap years rather than skipping the year or raising.
    """
    _validate_month_day(month, day)
    zone = ZoneInfo(timezone_name)
    wall = local_time or time(9, 0)
    local_after = after.astimezone(zone)

    def at_year(year: int) -> datetime:
        return datetime(
            year,
            month,
            clamp_month_day(year, month, day),
            wall.hour,
            wall.minute,
            tzinfo=zone,
        )

    candidate = at_year(local_after.year)
    if candidate <= local_after:
        candidate = at_year(local_after.year + 1)
    return candidate.astimezone(UTC)


def _validate_month_day(month: int, day: int) -> None:
    if not 1 <= month <= 12:
        raise ValueError("month must be 1..12")
    if not 1 <= day <= 31:
        raise ValueError("day must be 1..31")
    # Allow Feb 29; reject other impossible civil days (Apr 31, etc.).
    max_day = 29 if month == 2 else calendar.monthrange(2021, month)[1]
    if day > max_day:
        raise ValueError(f"day {day} is not valid for month {month}")


def _turns(year: int | None, next_at: datetime, timezone_name: str) -> int | None:
    if year is None:
        return None
    local_year = next_at.astimezone(ZoneInfo(timezone_name)).year
    age = local_year - year
    return age if age >= 0 else None


class ImportantDateStore:
    """Record and list annual dates backed by tasks and reminder jobs."""

    @staticmethod
    def record(
        connection: sqlite3.Connection,
        *,
        label: str,
        month: int,
        day: int,
        kind: DateKind = "birthday",
        year: int | None = None,
        destination: str | None = None,
        chat_id: int | None = None,
        timezone_name: str,
        now: datetime | None = None,
        remind_hour: int = 9,
        remind_minute: int = 0,
    ) -> ImportantDate:
        """Create or refresh one annual date as a task + repeating reminder."""
        cleaned = " ".join(label.split())
        if not cleaned:
            raise ValueError("important date needs a label")
        if kind not in {"birthday", "anniversary", "other"}:
            raise ValueError("kind must be birthday, anniversary, or other")
        if year is not None and year < 1:
            raise ValueError("year must be a positive calendar year when set")
        if destination is None:
            if chat_id is None:
                raise ValueError("important date destination is required")
            destination = f"telegram:{chat_id}"
        if not destination.strip() or ":" not in destination:
            raise ValueError("destination must be a non-empty channel:recipient value")
        # Validate the civil date once so Feb 29 is allowed and Apr 31 is refused.
        _validate_month_day(month, day)
        current = (now or datetime.now(UTC)).astimezone(UTC)
        wall = time(remind_hour, remind_minute)
        next_at = next_annual_occurrence(
            month, day, current, timezone_name=timezone_name, local_time=wall
        )
        title = _task_title(kind, cleaned)
        reminder_text = _reminder_text(kind, cleaned, year, next_at, timezone_name)
        idempotency_key = f"important-date:{kind}:{cleaned.lower()}:{month:02d}-{day:02d}"
        existing = connection.execute(
            "SELECT id, payload_json FROM jobs WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        stamped = datetime.now(UTC).isoformat()
        schedule = {
            "annual": True,
            "month": month,
            "day": day,
            "time": f"{remind_hour:02d}:{remind_minute:02d}",
            "timezone": timezone_name,
            "run_at": next_at.isoformat(),
        }
        if existing is not None:
            task_id = str(json.loads(existing["payload_json"])["task_id"])
            connection.execute(
                "UPDATE tasks SET title = ?, due_at = ?, updated_at = ? WHERE id = ?",
                (title, next_at.isoformat(), stamped, task_id),
            )
            payload = {
                "destination": destination,
                "task_id": task_id,
                "text": reminder_text,
                "date_kind": kind,
                "label": cleaned,
                "year": year,
            }
            connection.execute(
                """
                UPDATE jobs
                SET schedule_json = ?, next_run_at = ?, payload_json = ?,
                    updated_at = ?, state = 'active'
                WHERE id = ?
                """,
                (
                    json.dumps(schedule, sort_keys=True, separators=(",", ":")),
                    next_at.isoformat(),
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    stamped,
                    existing["id"],
                ),
            )
            job_id = str(existing["id"])
        else:
            event = EventStore.append(
                connection,
                source="important_date",
                external_id=f"{kind}:{cleaned.lower()}:{month:02d}-{day:02d}",
                occurred_at=current,
                content=title,
                metadata={"kind": kind, "month": month, "day": day, "year": year},
            )
            task = TaskStore.create(
                connection, title=title, source_event_id=event.id, due_at=next_at
            )
            task_id = task.id
            job_id = str(uuid4())
            payload = {
                "destination": destination,
                "task_id": task_id,
                "text": reminder_text,
                "date_kind": kind,
                "label": cleaned,
                "year": year,
            }
            connection.execute(
                """
                INSERT INTO jobs (
                    id, kind, schedule_json, next_run_at, state, payload_json,
                    idempotency_key, created_at, updated_at
                ) VALUES (?, 'reminder', ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    job_id,
                    json.dumps(schedule, sort_keys=True, separators=(",", ":")),
                    next_at.isoformat(),
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    idempotency_key,
                    stamped,
                    stamped,
                ),
            )
        return ImportantDate(
            job_id=job_id,
            task_id=task_id,
            label=cleaned,
            kind=kind,
            month=month,
            day=day,
            year=year,
            next_at=next_at,
            timezone=timezone_name,
            turns=_turns(year, next_at, timezone_name),
        )

    @staticmethod
    def list_all(database: Database) -> list[ImportantDate]:
        """Every active annual date, soonest next occurrence first."""
        database.migrate()
        with database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, schedule_json, payload_json, next_run_at
                FROM jobs
                WHERE kind = 'reminder' AND state = 'active' AND next_run_at IS NOT NULL
                ORDER BY next_run_at ASC
                """
            ).fetchall()
        dates: list[ImportantDate] = []
        for row in rows:
            parsed = _from_job_row(row)
            if parsed is not None:
                dates.append(parsed)
        return dates

    @staticmethod
    def upcoming(
        database: Database,
        *,
        within_days: int = 7,
        now: datetime | None = None,
    ) -> list[ImportantDate]:
        """Annual dates whose next occurrence falls inside the weekly window."""
        if within_days < 0:
            raise ValueError("within_days cannot be negative")
        current = (now or datetime.now(UTC)).astimezone(UTC)
        end = current + timedelta(days=within_days)
        return [
            item
            for item in ImportantDateStore.list_all(database)
            if current <= item.next_at <= end
        ]

    @staticmethod
    def advance_after_delivery(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        schedule: dict[str, Any],
        payload: dict[str, Any],
        after: datetime,
    ) -> datetime:
        """Roll an annual reminder and its task to the next year; return next UTC run."""
        next_at = next_annual_occurrence(
            int(schedule["month"]),
            int(schedule["day"]),
            after,
            timezone_name=str(schedule["timezone"]),
            local_time=_parse_hhmm(str(schedule.get("time", "09:00"))),
        )
        year = payload.get("year")
        kind_raw = str(payload.get("date_kind", "other"))
        kind: DateKind = (
            kind_raw if kind_raw in {"birthday", "anniversary", "other"} else "other"
        )
        label = str(payload.get("label", payload.get("text", "Important date")))
        reminder_text = _reminder_text(
            kind,
            label,
            int(year) if isinstance(year, int) else None,
            next_at,
            str(schedule["timezone"]),
        )
        new_payload = {**payload, "text": reminder_text}
        new_schedule = {**schedule, "run_at": next_at.isoformat()}
        stamped = datetime.now(UTC).isoformat()
        connection.execute(
            """
            UPDATE jobs
            SET schedule_json = ?, payload_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(new_schedule, sort_keys=True, separators=(",", ":")),
                json.dumps(new_payload, sort_keys=True, separators=(",", ":")),
                stamped,
                job_id,
            ),
        )
        task_id = payload.get("task_id")
        if isinstance(task_id, str) and task_id:
            connection.execute(
                "UPDATE tasks SET due_at = ?, updated_at = ? WHERE id = ?",
                (next_at.isoformat(), stamped, task_id),
            )
        return next_at


def annual_task_ids(database: Database) -> set[str]:
    """Task IDs owned by active annual dates — excluded from generic brief buckets."""
    return {item.task_id for item in ImportantDateStore.list_all(database)}


def _parse_hhmm(value: str) -> time:
    hour_text, minute_text = value.split(":", maxsplit=1)
    return time(int(hour_text), int(minute_text))


def _task_title(kind: str, label: str) -> str:
    if kind == "birthday":
        return f"Birthday: {label}"
    if kind == "anniversary":
        return f"Anniversary: {label}"
    return label


def _reminder_text(
    kind: str,
    label: str,
    year: int | None,
    next_at: datetime,
    timezone_name: str,
) -> str:
    turns = _turns(year, next_at, timezone_name)
    if kind == "birthday":
        if turns is not None:
            return f"{label}'s birthday (turns {turns})"
        return f"{label}'s birthday"
    if kind == "anniversary":
        if turns is not None:
            return f"{label} anniversary ({turns} years)"
        return f"{label} anniversary"
    return label


def _from_job_row(row: sqlite3.Row | Any) -> ImportantDate | None:
    schedule = json.loads(row["schedule_json"])
    if not schedule.get("annual"):
        return None
    payload = json.loads(row["payload_json"])
    kind_raw = payload.get("date_kind", "other")
    kind: DateKind = (
        kind_raw if kind_raw in {"birthday", "anniversary", "other"} else "other"
    )
    label = str(payload.get("label") or payload.get("text") or "Important date")
    next_at = datetime.fromisoformat(row["next_run_at"])
    timezone_name = str(schedule["timezone"])
    year = payload.get("year")
    year_value = int(year) if isinstance(year, int) else None
    return ImportantDate(
        job_id=row["id"],
        task_id=str(payload["task_id"]),
        label=label,
        kind=kind,
        month=int(schedule["month"]),
        day=int(schedule["day"]),
        year=year_value,
        next_at=next_at,
        timezone=timezone_name,
        turns=_turns(year_value, next_at, timezone_name),
    )
