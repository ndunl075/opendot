"""Persistent, timezone-aware daily morning-brief schedules."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo


def create_daily(
    connection: sqlite3.Connection,
    *,
    chat_id: int,
    local_time: time,
    timezone_name: str,
    now: datetime | None = None,
) -> str:
    """Create one daily delivery schedule, with the next occurrence stored in UTC."""
    timezone = ZoneInfo(timezone_name)
    current = (now or datetime.now(UTC)).astimezone(timezone)
    candidate = datetime.combine(current.date(), local_time, tzinfo=timezone)
    if candidate <= current:
        candidate += timedelta(days=1)
    job_id = str(uuid4())
    schedule = {"time": local_time.isoformat(timespec="minutes"), "timezone": timezone_name}
    connection.execute(
        """
        INSERT INTO jobs (id, kind, schedule_json, next_run_at, state, payload_json, idempotency_key, created_at, updated_at)
        VALUES (?, 'telegram_morning_brief', ?, ?, 'active', ?, ?, ?, ?)
        ON CONFLICT(idempotency_key) DO NOTHING
        """,
        (
            job_id,
            json.dumps(schedule, sort_keys=True),
            candidate.astimezone(UTC).isoformat(),
            json.dumps({"chat_id": chat_id}, sort_keys=True),
            f"daily-brief:{chat_id}:{timezone_name}:{schedule['time']}",
            datetime.now(UTC).isoformat(),
            datetime.now(UTC).isoformat(),
        ),
    )
    row = connection.execute(
        "SELECT id FROM jobs WHERE idempotency_key = ?", (f"daily-brief:{chat_id}:{timezone_name}:{schedule['time']}",)
    ).fetchone()
    if row is None:
        raise RuntimeError("morning brief schedule insert was ignored without an existing job")
    return str(row["id"])


def next_daily_occurrence(schedule: dict[str, str], after: datetime) -> datetime:
    """Return the first wall-clock schedule occurrence strictly after ``after``."""
    timezone = ZoneInfo(schedule["timezone"])
    hour, minute = (int(value) for value in schedule["time"].split(":"))
    local_after = after.astimezone(timezone)
    candidate = datetime.combine(local_after.date(), time(hour, minute), tzinfo=timezone)
    if candidate <= local_after:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)
