"""Birthdays and important dates over tasks + annual reminders."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alfred.briefing import BriefingService
from alfred.db import Database
from alfred.hermes_tools import is_casual_conversation, select_hermes_tools
from alfred.important_dates import (
    ImportantDateStore,
    clamp_month_day,
    next_annual_occurrence,
)
from alfred.jobs import JobRunner


def test_next_annual_occurrence_rolls_past_today_and_clamps_leap_day() -> None:
    assert clamp_month_day(2025, 2, 29) == 28
    assert clamp_month_day(2024, 2, 29) == 29
    # After March 1 2026, next Feb 29 (stored as 29) lands on Feb 28 2027.
    next_at = next_annual_occurrence(
        2,
        29,
        datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        timezone_name="America/New_York",
    )
    assert next_at == datetime(2027, 2, 28, 14, 0, tzinfo=UTC)


def test_april_31_is_refused() -> None:
    with pytest.raises(ValueError, match="not valid"):
        next_annual_occurrence(4, 31, datetime(2026, 1, 1, tzinfo=UTC), timezone_name="UTC")


def test_recording_a_birthday_creates_a_task_and_annual_reminder(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            recorded = ImportantDateStore.record(
                connection,
                label="Mom",
                month=8,
                day=20,
                kind="birthday",
                year=1970,
                chat_id=20,
                timezone_name="America/New_York",
                now=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
            )

    assert recorded.kind == "birthday"
    assert recorded.turns == 56  # 2026 - 1970
    assert recorded.next_at == datetime(2026, 8, 20, 13, 0, tzinfo=UTC)  # 09:00 EDT
    with database.connect() as connection:
        task = connection.execute(
            "SELECT title, due_at FROM tasks WHERE id = ?", (recorded.task_id,)
        ).fetchone()
        job = connection.execute(
            "SELECT schedule_json, payload_json, state FROM jobs WHERE id = ?",
            (recorded.job_id,),
        ).fetchone()
    assert task["title"] == "Birthday: Mom"
    assert json.loads(job["schedule_json"])["annual"] is True
    assert json.loads(job["payload_json"])["text"] == "Mom's birthday (turns 56)"
    assert job["state"] == "active"


def test_re_recording_the_same_birthday_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            first = ImportantDateStore.record(
                connection,
                label="Mom",
                month=8,
                day=20,
                chat_id=20,
                timezone_name="America/New_York",
                now=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
            )
            second = ImportantDateStore.record(
                connection,
                label="Mom",
                month=8,
                day=20,
                year=1970,
                chat_id=20,
                timezone_name="America/New_York",
                now=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
            )
    assert first.job_id == second.job_id
    assert first.task_id == second.task_id
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_annual_reminder_delivers_and_rolls_to_next_year(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            recorded = ImportantDateStore.record(
                connection,
                label="Dad",
                month=8,
                day=14,
                kind="birthday",
                year=1965,
                chat_id=20,
                timezone_name="UTC",
                now=datetime(2026, 8, 1, 0, 0, tzinfo=UTC),
                remind_hour=9,
                remind_minute=0,
            )

    executed = JobRunner(database).run_due(datetime(2026, 8, 14, 9, 0, tzinfo=UTC))

    assert len(executed) == 1
    with database.connect() as connection:
        job = connection.execute(
            "SELECT state, next_run_at, payload_json FROM jobs WHERE id = ?",
            (recorded.job_id,),
        ).fetchone()
        task = connection.execute(
            "SELECT due_at FROM tasks WHERE id = ?", (recorded.task_id,)
        ).fetchone()
        message = json.loads(
            connection.execute(
                "SELECT payload_json FROM outbox WHERE job_id = ?", (recorded.job_id,)
            ).fetchone()[0]
        )
    assert job["state"] == "active"
    assert datetime.fromisoformat(job["next_run_at"]) == datetime(2027, 8, 14, 9, 0, tzinfo=UTC)
    assert datetime.fromisoformat(task["due_at"]) == datetime(2027, 8, 14, 9, 0, tzinfo=UTC)
    assert "Dad's birthday (turns 61)" in message["text"]
    # Payload text for *next* year was advanced after delivery.
    assert json.loads(job["payload_json"])["text"] == "Dad's birthday (turns 62)"


def test_morning_brief_surfaces_birthdays_in_the_weekly_window(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            ImportantDateStore.record(
                connection,
                label="Mom",
                month=8,
                day=18,
                kind="birthday",
                year=1970,
                chat_id=20,
                timezone_name="UTC",
                now=datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
            )

    brief = BriefingService(database).morning_brief(
        datetime(2026, 8, 14, 8, 0, tzinfo=UTC), timezone_name="UTC"
    )

    assert [item.title for item in brief.important_dates] == ["Mom's birthday (turns 56)"]
    assert "Birthdays & dates:" in brief.render()
    # Not double-listed as ordinary homework.
    assert brief.upcoming == []
    assert brief.due_today == []


def test_birthday_phrases_select_important_date_tools() -> None:
    phrase = "remember mom's birthday is august 20 1970"
    tools = select_hermes_tools(phrase)
    assert "important_date_set" in tools
    assert not is_casual_conversation(phrase)
