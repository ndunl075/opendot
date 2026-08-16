"""Mood check-ins, gratitude journal, and guarded mood trends."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alfred.db import Database
from alfred.hermes_tools import (
    MAX_HERMES_TOOLS_PER_TURN,
    is_casual_conversation,
    select_hermes_tools,
)
from alfred.journal import JournalStore, MIN_MOOD_DAYS_FOR_TREND, MIN_MOOD_SPREAD
from alfred.mcp_server import MCP_TOOL_NAMES, create_server
from alfred.policy import PolicyStore
from tests.test_mcp_server import _call, _grant


def test_mood_rating_must_be_on_the_one_to_five_scale(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with pytest.raises(ValueError, match="between 1 and 5"):
            JournalStore.mood_record(connection, rating=0)
        with pytest.raises(ValueError, match="between 1 and 5"):
            JournalStore.mood_record(connection, rating=6)


def test_gratitude_entry_requires_text(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with pytest.raises(ValueError, match="needs text"):
            JournalStore.gratitude_record(connection, text="   ")


def test_journal_get_returns_entries_and_declines_trend_without_enough_days(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    with database.connect() as connection:
        with database.transaction(connection):
            for offset in range(3):
                JournalStore.mood_record(
                    connection,
                    rating=3,
                    recorded_at=base + timedelta(days=offset),
                )
            JournalStore.gratitude_record(connection, text="Morning coffee")

    snapshot = JournalStore.get(database, days=30, now=base + timedelta(days=10))

    assert len(snapshot.moods) == 3
    assert snapshot.gratitude[0].text == "Morning coffee"
    assert snapshot.mood_trend.direction is None
    assert snapshot.mood_trend.reason is not None
    assert str(MIN_MOOD_DAYS_FOR_TREND) in snapshot.mood_trend.reason


def test_mood_trend_refuses_low_spread(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    with database.connect() as connection:
        with database.transaction(connection):
            for offset in range(5):
                JournalStore.mood_record(
                    connection,
                    rating=3,
                    recorded_at=base + timedelta(days=offset),
                )

    snapshot = JournalStore.get(database, days=30, now=base + timedelta(days=10))

    assert snapshot.mood_trend.direction is None
    assert snapshot.mood_trend.reason is not None
    assert str(MIN_MOOD_SPREAD) in snapshot.mood_trend.reason


def test_mood_trend_names_direction_when_data_is_sufficient(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    ratings = [2, 2, 2, 4, 5, 5]
    with database.connect() as connection:
        with database.transaction(connection):
            for offset, rating in enumerate(ratings):
                JournalStore.mood_record(
                    connection,
                    rating=rating,
                    recorded_at=base + timedelta(days=offset),
                )

    snapshot = JournalStore.get(database, days=30, now=base + timedelta(days=10))

    assert snapshot.mood_trend.direction == "up"
    assert snapshot.mood_trend.reason is None


def test_mood_and_gratitude_round_trip_through_mcp(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(
        database_path,
        allowed_tools={"mood_record", "gratitude_record", "journal_get"},
    )
    server = create_server(database_path)

    mood = _call(server, "mood_record", {"rating": 4, "note": "steady day"})
    gratitude = _call(server, "gratitude_record", {"text": "a quiet morning"})
    journal = _call(server, "journal_get", {"days": 7})

    assert mood["rating"] == 4
    assert mood["note"] == "steady day"
    assert gratitude["text"] == "a quiet morning"
    assert journal["moods"][0]["id"] == mood["id"]
    assert journal["gratitude"][0]["id"] == gratitude["id"]
    assert journal["mood_trend"]["direction"] is None
    assert journal["mood_trend"]["reason"] is not None


def test_mood_phrases_select_journal_tools() -> None:
    tools = select_hermes_tools("log my mood as a 4 today")
    assert "mood_record" in tools
    assert "journal_get" in tools
    assert not is_casual_conversation("how am i feeling today?")


def test_gratitude_phrases_select_journal_tools() -> None:
    tools = select_hermes_tools("gratitude journal: my family")
    assert "gratitude_record" in tools
    assert "journal_get" in tools


# One focused phrase per tool that the Hermes router can select. Catches tools
# accidentally dropped from _TOOL_PRIORITY when a new write tool is added.
_SELECTABLE_TOOL_PHRASES: dict[str, str] = {
    "system_status": "check system status",
    "connector_status": "connector health status",
    "agenda_get": "what's on my task list",
    "brief_get": "what should i work on today",
    "memory_search": "search my memory",
    "profile_get": "what do you remember about me",
    "remember": "remember that I like tea",
    "memory_correct": "that memory is incorrect, update it",
    "memory_feedback": "that memory is incorrect, update it",
    "forget": "forget that memory",
    "calendar_event_propose": "create a calendar event for lunch",
    "message_draft": "draft a reply to that email",
    "message_send_propose": "send them an email",
    "github_issue_propose": "file a github issue about the bug",
    "connector_records_get": "what's on my calendar schedule",
    "task_upsert": "add a task to call mom",
    "task_complete": "mark the task done",
    "reminder_set": "remind me at 3pm",
    "task_schedule": "check at 3pm and text me the score",
    "important_date_set": "remember mom's birthday is august 20",
    "important_dates_get": "upcoming birthdays this week",
    "threads_awaiting_reply": "threads awaiting my reply",
    "availability_get": "when am I free on my calendar",
    "pull_requests_get": "my open pull requests",
    "mood_record": "log my mood today",
    "gratitude_record": "gratitude journal: my friends",
    "journal_get": "show my mood trend",
}


def test_every_selectable_tool_survives_the_priority_trim() -> None:
    for tool_name, phrase in _SELECTABLE_TOOL_PHRASES.items():
        selected = select_hermes_tools(phrase)
        assert tool_name in selected, f"{tool_name} missing for phrase: {phrase!r}"
        assert len(selected) <= MAX_HERMES_TOOLS_PER_TURN

    # action_commit is added by the GitHub write router but is intentionally
    # absent from _TOOL_PRIORITY, so it never survives the ordered trim. Do
    # not add it ahead of safer write tools.
    github_tools = select_hermes_tools("file a github issue about the bug")
    assert "github_issue_propose" in github_tools
    assert "action_commit" not in github_tools
    broad = select_hermes_tools(
        "create a calendar event, remind me, send email, file a github issue, "
        "correct memory, and show connector status"
    )
    assert "action_commit" not in broad

    assert set(_SELECTABLE_TOOL_PHRASES) | {"action_commit"} <= MCP_TOOL_NAMES
