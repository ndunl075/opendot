from datetime import UTC, datetime
import json
from pathlib import Path

import httpx

from alfred.db import Database
from alfred.google_calendar import GoogleCalendarClient, GoogleCalendarSync, SyncTokenExpired


def _event(event_id: str, updated: str, summary: str = "Study group") -> dict:
    return {
        "id": event_id,
        "updated": updated,
        "summary": summary,
        "status": "confirmed",
        "start": {"dateTime": "2026-08-15T10:00:00-04:00"},
        "end": {"dateTime": "2026-08-15T11:00:00-04:00"},
        "description": "This intentionally must not be stored.",
        "attendees": [{"email": "private@example.com"}],
    }


class FakeCalendar:
    def __init__(self) -> None:
        self.calls: list[str | None] = []

    def list_events(self, *, calendar_id, sync_token, time_min, time_max):
        self.calls.append(sync_token)
        return [_event("event-1", "2026-08-12T12:00:00Z")], "cursor-1"


def test_sync_stores_minimized_immutable_calendar_events_and_cursor(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    fake = FakeCalendar()

    first = GoogleCalendarSync(database, fake).sync(
        time_min=datetime(2026, 8, 10, tzinfo=UTC), time_max=datetime(2026, 8, 24, tzinfo=UTC)
    )
    second = GoogleCalendarSync(database, fake).sync(
        time_min=datetime(2026, 8, 10, tzinfo=UTC), time_max=datetime(2026, 8, 24, tzinfo=UTC)
    )

    assert (first.received, first.stored, second.stored) == (1, 1, 0)
    assert fake.calls == [None, "cursor-1"]
    with database.connect() as connection:
        event = connection.execute("SELECT * FROM events WHERE source = 'google_calendar'").fetchone()
        metadata = json.loads(event["metadata_json"])
        assert event["content"] == "Study group"
        assert "description" not in event["metadata_json"]
        assert "private@example.com" not in event["metadata_json"]
        assert metadata["calendar_event_id"] == "event-1"
        assert connection.execute("SELECT cursor FROM sync_state WHERE connector = 'google_calendar'").fetchone()[0] == "cursor-1"
        assert connection.execute(
            "SELECT active FROM connector_records WHERE connector = 'google_calendar' AND record_id = 'event-1'"
        ).fetchone()[0] == 1


def test_incremental_cancelled_event_is_removed_from_current_projection(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")

    class Changes:
        def __init__(self):
            self.calls = 0

        def list_events(self, *, calendar_id, sync_token, time_min, time_max):
            self.calls += 1
            if self.calls == 1:
                return [_event("event-4", "2026-08-12T12:00:00Z")], "cursor-4"
            cancelled = _event("event-4", "2026-08-13T12:00:00Z")
            cancelled["status"] = "cancelled"
            return [cancelled], "cursor-5"

    sync = GoogleCalendarSync(database, Changes())
    sync.sync()
    sync.sync()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT active FROM connector_records WHERE connector = 'google_calendar' AND record_id = 'event-4'"
        ).fetchone()[0] == 0


def test_expired_cursor_resets_to_full_sync(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")

    class ExpiringCalendar(FakeCalendar):
        def list_events(self, *, calendar_id, sync_token, time_min, time_max):
            self.calls.append(sync_token)
            if sync_token:
                raise SyncTokenExpired()
            return [_event("event-2", "2026-08-13T12:00:00Z")], "fresh-cursor"

    initial = FakeCalendar()
    GoogleCalendarSync(database, initial).sync()
    expiring = ExpiringCalendar()
    result = GoogleCalendarSync(database, expiring).sync()

    assert result.reset_cursor is True
    assert expiring.calls == ["cursor-1", None]
    assert result.next_cursor == "fresh-cursor"


def test_http_client_uses_read_only_events_list_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer TOKEN"
        assert request.url.path == "/calendar/v3/calendars/primary/events"
        assert request.url.params["singleEvents"] == "true"
        assert request.url.params["showDeleted"] == "true"
        assert request.url.params["orderBy"] == "startTime"
        return httpx.Response(200, json={"items": [_event("event-3", "2026-08-12T12:00:00Z")], "nextSyncToken": "next"})

    client = GoogleCalendarClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        events, cursor = client.list_events(
            calendar_id="primary", sync_token=None, time_min=datetime(2026, 8, 10, tzinfo=UTC), time_max=None
        )
    finally:
        client.close()
    assert events[0]["id"] == "event-3"
    assert cursor == "next"
