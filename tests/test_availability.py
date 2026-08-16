"""Calendar availability: free gaps over synced timed events."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from alfred.availability import AvailabilityService
from alfred.db import Database


def _seed_events(database: Database, events: list[dict]) -> None:
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            for index, event in enumerate(events):
                connection.execute(
                    """
                    INSERT INTO connector_records (
                        connector, account, record_type, record_id,
                        payload_json, observed_at, active
                    ) VALUES ('google_calendar', 'primary', 'event', ?, ?, ?, 1)
                    """,
                    (
                        str(index),
                        json.dumps(event, sort_keys=True),
                        "2026-08-14T08:00:00+00:00",
                    ),
                )


def test_availability_merges_overlaps_and_finds_gaps(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _seed_events(
        database,
        [
            {
                "title": "Standup",
                "start": "2026-08-14T13:00:00+00:00",
                "end": "2026-08-14T13:30:00+00:00",
                "calendar_id": "primary",
            },
            {
                "title": "Overlap",
                "start": "2026-08-14T13:15:00+00:00",
                "end": "2026-08-14T14:00:00+00:00",
                "calendar_id": "primary",
            },
            {
                "title": "Later",
                "start": "2026-08-14T16:00:00+00:00",
                "end": "2026-08-14T17:00:00+00:00",
                "calendar_id": "primary",
            },
        ],
    )

    report = AvailabilityService(database).get(
        days=1,
        timezone_name="UTC",
        now=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
        min_minutes=30,
    )

    assert [block.title for block in report.busy] == ["Standup; Overlap", "Later"]
    assert [(slot.start.hour, slot.end.hour) for slot in report.free] == [(9, 13), (14, 16)]


def test_all_day_events_are_notes_not_busy_hours(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _seed_events(
        database,
        [
            {
                "title": "Cincinnati Open - Dad/Alex",
                "start": "2026-08-15",
                "end": "2026-08-16",
                "calendar_id": "primary",
            },
            {
                "title": "Practice",
                "start": "2026-08-15T14:00:00+00:00",
                "end": "2026-08-15T16:00:00+00:00",
                "calendar_id": "primary",
            },
        ],
    )

    report = AvailabilityService(database).get(
        days=2,
        timezone_name="UTC",
        now=datetime(2026, 8, 15, 9, 0, tzinfo=UTC),
    )

    assert [note.title for note in report.all_day] == ["Cincinnati Open - Dad/Alex"]
    assert "ambiguous" in report.render().lower() or "All-day" in report.render()
    assert any(block.title == "Practice" for block in report.busy)
