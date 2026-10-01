"""Mood check-ins and gratitude journal — separate from habits.

Habits answer whether something happened and track streaks. Mood is a 1–5
rating with optional context; gratitude is free text. Neither belongs on the
habits table because a free-text rating cannot be averaged like a boolean
streak, and the trend logic needs its own guardrails.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel

from .db import Database

MIN_MOOD_DAYS_FOR_TREND = 5
MIN_MOOD_SPREAD = 0.5


class MoodEntry(BaseModel):
    id: str
    rating: int
    note: str | None = None
    recorded_at: datetime


class GratitudeEntry(BaseModel):
    id: str
    text: str
    recorded_at: datetime


class MoodTrend(BaseModel):
    direction: Literal["up", "down", "flat"] | None = None
    reason: str | None = None


class JournalSnapshot(BaseModel):
    moods: list[MoodEntry]
    gratitude: list[GratitudeEntry]
    mood_trend: MoodTrend


class JournalStore:
    @staticmethod
    def mood_record(
        connection: sqlite3.Connection,
        *,
        rating: int,
        note: str | None = None,
        recorded_at: datetime | None = None,
    ) -> MoodEntry:
        if not 1 <= rating <= 5:
            raise ValueError("mood rating must be between 1 and 5")
        cleaned_note = " ".join(note.split()) if note else None
        if cleaned_note == "":
            cleaned_note = None
        stamped = (recorded_at or datetime.now(UTC)).astimezone(UTC)
        entry_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO mood_entries (id, rating, note, recorded_at)
            VALUES (?, ?, ?, ?)
            """,
            (entry_id, rating, cleaned_note, stamped.isoformat()),
        )
        return MoodEntry(id=entry_id, rating=rating, note=cleaned_note, recorded_at=stamped)

    @staticmethod
    def gratitude_record(
        connection: sqlite3.Connection,
        *,
        text: str,
        recorded_at: datetime | None = None,
    ) -> GratitudeEntry:
        cleaned = " ".join(text.split())
        if not cleaned:
            raise ValueError("gratitude entry needs text")
        stamped = (recorded_at or datetime.now(UTC)).astimezone(UTC)
        entry_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO gratitude_entries (id, text, recorded_at)
            VALUES (?, ?, ?)
            """,
            (entry_id, cleaned, stamped.isoformat()),
        )
        return GratitudeEntry(id=entry_id, text=cleaned, recorded_at=stamped)

    @staticmethod
    def get(
        database: Database,
        *,
        days: int = 30,
        now: datetime | None = None,
    ) -> JournalSnapshot:
        if days < 1:
            raise ValueError("days must be at least 1")
        database.migrate()
        current = (now or datetime.now(UTC)).astimezone(UTC)
        since = current - timedelta(days=days)
        with database.connect() as connection:
            mood_rows = connection.execute(
                """
                SELECT id, rating, note, recorded_at
                FROM mood_entries
                WHERE recorded_at >= ?
                ORDER BY recorded_at DESC
                """,
                (since.isoformat(),),
            ).fetchall()
            gratitude_rows = connection.execute(
                """
                SELECT id, text, recorded_at
                FROM gratitude_entries
                WHERE recorded_at >= ?
                ORDER BY recorded_at DESC
                """,
                (since.isoformat(),),
            ).fetchall()
        moods = [_mood_from_row(row) for row in mood_rows]
        gratitude = [_gratitude_from_row(row) for row in gratitude_rows]
        return JournalSnapshot(
            moods=moods,
            gratitude=gratitude,
            mood_trend=_mood_trend(moods),
        )


def _mood_from_row(row: sqlite3.Row | Any) -> MoodEntry:
    return MoodEntry(
        id=row["id"],
        rating=int(row["rating"]),
        note=row["note"],
        recorded_at=datetime.fromisoformat(row["recorded_at"]),
    )


def _gratitude_from_row(row: sqlite3.Row | Any) -> GratitudeEntry:
    return GratitudeEntry(
        id=row["id"],
        text=row["text"],
        recorded_at=datetime.fromisoformat(row["recorded_at"]),
    )


def _mood_trend(moods: list[MoodEntry]) -> MoodTrend:
    if not moods:
        return MoodTrend(
            reason="no mood check-ins recorded yet",
        )

    daily: dict[date, list[int]] = {}
    for entry in moods:
        day = entry.recorded_at.astimezone(UTC).date()
        daily.setdefault(day, []).append(entry.rating)

    sorted_days = sorted(daily)
    if len(sorted_days) < MIN_MOOD_DAYS_FOR_TREND:
        day_word = "day" if len(sorted_days) == 1 else "days"
        return MoodTrend(
            reason=(
                f"only {len(sorted_days)} {day_word} with mood check-ins; "
                f"need at least {MIN_MOOD_DAYS_FOR_TREND} to name a trend"
            ),
        )

    daily_averages = [sum(daily[day]) / len(daily[day]) for day in sorted_days]
    midpoint = len(daily_averages) // 2
    older_avg = sum(daily_averages[:midpoint]) / midpoint
    newer_avg = sum(daily_averages[midpoint:]) / (len(daily_averages) - midpoint)
    spread = abs(newer_avg - older_avg)

    if spread < MIN_MOOD_SPREAD:
        return MoodTrend(
            reason=(
                f"mood spread {spread:.1f} is below {MIN_MOOD_SPREAD:g} points; "
                "not enough change to call a trend"
            ),
        )

    if newer_avg > older_avg:
        direction: Literal["up", "down", "flat"] = "up"
    elif newer_avg < older_avg:
        direction = "down"
    else:
        direction = "flat"
    return MoodTrend(direction=direction)
