"""Find free gaps in the owner's synced Google Calendar.

Interval-merge over active timed events in a local window. Rules:

- Cancelled events never appear in the active connector snapshot, so they are
  already excluded by reading ``active = 1``.
- All-day events (``start``/``end`` with no ``T``, i.e. a bare date) do not
  block the day the way a timed meeting does. They are reported separately as
  ambiguous all-day context so the owner can decide (a tournament day is not
  the same as "busy 9–5").
- Overlapping timed events merge before gaps are computed, so stacked meetings
  do not invent tiny free slots between them.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from .db import Database


class BusyBlock(BaseModel):
    title: str
    start: datetime
    end: datetime
    calendar_id: str | None = None


class FreeSlot(BaseModel):
    start: datetime
    end: datetime

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


class AllDayNote(BaseModel):
    title: str
    day: date
    calendar_id: str | None = None


class AvailabilityReport(BaseModel):
    timezone: str
    window_start: datetime
    window_end: datetime
    free: list[FreeSlot]
    busy: list[BusyBlock]
    all_day: list[AllDayNote] = []

    def render(self) -> str:
        lines = [
            f"Availability — {self.window_start.date().isoformat()} to {self.window_end.date().isoformat()} ({self.timezone})",
        ]
        if self.all_day:
            lines.append("\nAll-day (ambiguous — not treated as busy hours):")
            for note in self.all_day:
                cal = f" [{note.calendar_id}]" if note.calendar_id else ""
                lines.append(f"- {note.day.isoformat()}: {note.title}{cal}")
        if self.free:
            lines.append("\nFree:")
            for slot in self.free:
                lines.append(
                    f"- {slot.start.strftime('%a %Y-%m-%d %H:%M')}–{slot.end.strftime('%H:%M')} "
                    f"({_fmt_duration(slot.duration)})"
                )
        else:
            lines.append("\nNo free gaps in this window.")
        if self.busy:
            lines.append("\nBusy:")
            for block in self.busy:
                lines.append(
                    f"- {block.start.strftime('%a %H:%M')}–{block.end.strftime('%H:%M')}: {block.title}"
                )
        return "\n".join(lines)


class AvailabilityService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get(
        self,
        *,
        days: int = 7,
        timezone_name: str = "UTC",
        day_start: time | None = None,
        day_end: time | None = None,
        now: datetime | None = None,
        min_minutes: int = 30,
    ) -> AvailabilityReport:
        """Return free slots inside local working hours for the next ``days`` days."""
        if days < 1:
            raise ValueError("days must be at least 1")
        if min_minutes < 1:
            raise ValueError("min_minutes must be at least 1")
        zone = ZoneInfo(timezone_name)
        current = (now or datetime.now(UTC)).astimezone(zone)
        work_start = day_start or time(9, 0)
        work_end = day_end or time(17, 0)
        if work_start >= work_end:
            raise ValueError("day_start must be before day_end")
        window_start = datetime.combine(current.date(), work_start, tzinfo=zone)
        if window_start < current:
            window_start = current
        window_end = datetime.combine(
            current.date() + timedelta(days=days - 1), work_end, tzinfo=zone
        )
        self.database.migrate()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM connector_records
                WHERE connector = 'google_calendar' AND record_type = 'event' AND active = 1
                """
            ).fetchall()

        busy: list[BusyBlock] = []
        all_day: list[AllDayNote] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            title = str(payload.get("title") or "Untitled event")
            calendar_id = payload.get("calendar_id")
            calendar_id = calendar_id if isinstance(calendar_id, str) else None
            start_raw = payload.get("start")
            end_raw = payload.get("end")
            if not isinstance(start_raw, str) or not start_raw:
                continue
            if _is_all_day(start_raw):
                day = date.fromisoformat(start_raw[:10])
                if current.date() <= day <= window_end.date():
                    all_day.append(AllDayNote(title=title, day=day, calendar_id=calendar_id))
                continue
            start = _parse_instant(start_raw, zone)
            end = _parse_instant(end_raw, zone) if isinstance(end_raw, str) and end_raw else start
            if end <= window_start or start >= window_end:
                continue
            busy.append(
                BusyBlock(
                    title=title,
                    start=max(start, window_start),
                    end=min(end, window_end),
                    calendar_id=calendar_id,
                )
            )

        busy.sort(key=lambda block: (block.start, block.end, block.title))
        merged = _merge_busy(busy)
        free = _gaps(
            merged,
            window_start=window_start,
            window_end=window_end,
            zone=zone,
            work_start=work_start,
            work_end=work_end,
            min_gap=timedelta(minutes=min_minutes),
            now=current,
        )
        all_day.sort(key=lambda note: (note.day, note.title))
        return AvailabilityReport(
            timezone=timezone_name,
            window_start=window_start,
            window_end=window_end,
            free=free,
            busy=merged,
            all_day=all_day,
        )


def _is_all_day(value: str) -> bool:
    return "T" not in value


def _parse_instant(value: str, zone: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _merge_busy(blocks: list[BusyBlock]) -> list[BusyBlock]:
    if not blocks:
        return []
    merged: list[BusyBlock] = [blocks[0]]
    for block in blocks[1:]:
        last = merged[-1]
        if block.start <= last.end:
            if block.end > last.end or (block.end == last.end and block.title != last.title):
                title = last.title if block.title == last.title else f"{last.title}; {block.title}"
                merged[-1] = BusyBlock(
                    title=title,
                    start=last.start,
                    end=max(last.end, block.end),
                    calendar_id=last.calendar_id or block.calendar_id,
                )
        else:
            merged.append(block)
    return merged


def _gaps(
    busy: list[BusyBlock],
    *,
    window_start: datetime,
    window_end: datetime,
    zone: ZoneInfo,
    work_start: time,
    work_end: time,
    min_gap: timedelta,
    now: datetime,
) -> list[FreeSlot]:
    free: list[FreeSlot] = []
    day = window_start.date()
    last_day = window_end.date()
    while day <= last_day:
        day_start = datetime.combine(day, work_start, tzinfo=zone)
        day_end = datetime.combine(day, work_end, tzinfo=zone)
        cursor = max(day_start, now, window_start)
        end_bound = min(day_end, window_end)
        day_busy = [block for block in busy if block.start < end_bound and block.end > cursor]
        for block in day_busy:
            if block.start > cursor:
                candidate_end = min(block.start, end_bound)
                if candidate_end - cursor >= min_gap:
                    free.append(FreeSlot(start=cursor, end=candidate_end))
            cursor = max(cursor, block.end)
        if end_bound - cursor >= min_gap:
            free.append(FreeSlot(start=cursor, end=end_bound))
        day += timedelta(days=1)
    return free


def _fmt_duration(value: timedelta) -> str:
    minutes = int(value.total_seconds() // 60)
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"
