from datetime import UTC, datetime
import json
from pathlib import Path

import httpx
import pytest

from alfred.db import Database
from alfred.google_calendar import (
    CalendarCatalogSync,
    GoogleCalendarActions,
    GoogleCalendarClient,
    GoogleCalendarHistorySync,
    GoogleCalendarSync,
    SyncTokenExpired,
)
from alfred.policy import ApprovalService, PolicyError


def _event(event_id: str, updated: str, summary: str = "Study group") -> dict:
    return {
        "id": event_id,
        "updated": updated,
        "summary": summary,
        "status": "confirmed",
        "start": {"dateTime": "2026-08-15T10:00:00-04:00"},
        "end": {"dateTime": "2026-08-15T11:00:00-04:00"},
        "creator": {"displayName": "Professor Example", "email": "prof@example.edu", "self": False},
        "organizer": {"email": "course@example.edu", "self": False},
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
        assert metadata["creator"] == {
            "displayName": "Professor Example",
            "email": "prof@example.edu",
            "self": False,
        }
        assert metadata["organizer"] == {"email": "course@example.edu", "self": False}
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


def test_history_backfill_does_not_create_or_replace_live_cursor(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    fake = FakeCalendar()
    result = GoogleCalendarHistorySync(database, fake).sync(
        calendar_id="primary",
        time_min=datetime(2024, 1, 1, tzinfo=UTC),
        time_max=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert (result.received, result.stored) == (1, 1)
    assert fake.calls == [None]
    with database.connect() as connection:
        history = connection.execute(
            "SELECT last_success_at FROM sync_state WHERE connector = 'google_calendar_history' AND account = 'primary'"
        ).fetchone()
        live = connection.execute(
            "SELECT cursor FROM sync_state WHERE connector = 'google_calendar' AND account = 'primary'"
        ).fetchone()
    assert history["last_success_at"] is not None
    assert live is None


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


def test_http_client_encodes_hash_in_holiday_calendar_id() -> None:
    calendar_id = "en.usa#holiday@group.v.calendar.google.com"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.startswith(
            b"/calendar/v3/calendars/en.usa%23holiday%40group.v.calendar.google.com/events"
        )
        return httpx.Response(200, json={"items": [], "nextSyncToken": "next"})

    client = GoogleCalendarClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        events, cursor = client.list_events(
            calendar_id=calendar_id,
            sync_token=None,
            time_min=datetime(2026, 8, 10, tzinfo=UTC),
            time_max=None,
        )
    finally:
        client.close()

    assert events == []
    assert cursor == "next"


def test_http_client_lists_calendars_selected_in_the_google_ui() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/calendar/v3/users/me/calendarList"
        assert request.url.params["showHidden"] == "false"
        assert request.url.params["minAccessRole"] == "reader"
        return httpx.Response(
            200,
            json={"items": [{"id": "school@example.com", "summary": "School", "selected": True}]},
        )

    client = GoogleCalendarClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        calendars = client.list_calendars()
    finally:
        client.close()
    assert calendars[0]["id"] == "school@example.com"


def test_catalog_sync_keeps_primary_and_selected_calendars_only(tmp_path: Path) -> None:
    class Catalog:
        def list_calendars(self):
            return [
                {"id": "owner@example.com", "summary": "Personal", "primary": True},
                {
                    "id": "school@example.com",
                    "summary": "School",
                    "selected": True,
                    "dataOwner": "school-owner@example.com",
                    "accessRole": "reader",
                },
                {"id": "hidden@example.com", "summary": "Hidden", "selected": False},
            ]

    database = Database(tmp_path / "alfred.db")
    selected = CalendarCatalogSync(database, Catalog()).sync()

    assert [item["title"] for item in selected] == ["Personal", "School"]
    assert selected[1]["data_owner"] == "school-owner@example.com"
    assert selected[1]["access_role"] == "reader"
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT record_id FROM connector_records
            WHERE connector='google_calendar' AND record_type='calendar' AND active=1
            ORDER BY record_id
            """
        ).fetchall()
        state = connection.execute(
            """
            SELECT last_success_at FROM sync_state
            WHERE connector='google_calendar_catalog' AND account='self'
            """
        ).fetchone()
    assert [row["record_id"] for row in rows] == ["owner@example.com", "school@example.com"]
    assert state["last_success_at"] is not None


class FakeWriteTransport:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create_event(self, *, calendar_id, event_id, summary, start, end):
        self.calls.append({"calendar_id": calendar_id, "event_id": event_id, "summary": summary, "start": start, "end": end})
        return {"id": event_id, "htmlLink": "https://calendar.google.com/event?eid=abc"}


def test_calendar_event_is_never_created_without_a_consumed_approval(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    approvals = ApprovalService(database)
    transport = FakeWriteTransport()
    actions = GoogleCalendarActions(database, approvals, transport)
    start = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
    end = datetime(2026, 8, 15, 11, 0, tzinfo=UTC)

    proposed = actions.propose_event(actor="nico", calendar_id="primary", summary="Advisor meeting", start=start, end=end)
    assert transport.calls == []  # proposing alone must never touch Google

    with pytest.raises(PolicyError, match="not usable"):
        actions.execute(proposed.id, actor="nico", token="not-a-real-token")
    assert transport.calls == []

    issued = approvals.approve(proposed.id, actor="nico")
    receipt = actions.execute(proposed.id, actor="nico", token=issued.token)

    assert receipt.replayed is False
    assert receipt.calendar_event_id == transport.calls[0]["event_id"]
    assert transport.calls == [{"calendar_id": "primary", "event_id": transport.calls[0]["event_id"], "summary": "Advisor meeting", "start": start, "end": end}]
    with database.connect() as connection:
        approval_state = connection.execute("SELECT state FROM approvals WHERE id = ?", (proposed.id,)).fetchone()[0]
    assert approval_state == "consumed"


def test_calendar_execute_replays_the_receipt_instead_of_creating_twice(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    approvals = ApprovalService(database)
    transport = FakeWriteTransport()
    actions = GoogleCalendarActions(database, approvals, transport)
    start = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
    end = datetime(2026, 8, 15, 11, 0, tzinfo=UTC)
    proposed = actions.propose_event(actor="nico", calendar_id="primary", summary="Advisor meeting", start=start, end=end)
    issued = approvals.approve(proposed.id, actor="nico")

    first = actions.execute(proposed.id, actor="nico", token=issued.token)
    second = actions.execute(proposed.id, actor="nico", token=issued.token)

    assert first.replayed is False
    assert second.replayed is True
    assert second.calendar_event_id == first.calendar_event_id
    assert len(transport.calls) == 1  # Google was only ever asked to create the event once

    with pytest.raises(PolicyError, match="does not match"):
        actions.execute(proposed.id, actor="someone-else", token=issued.token)
    with pytest.raises(PolicyError, match="invalid"):
        actions.execute(proposed.id, actor="nico", token="wrong-token")


def test_calendar_execute_recovers_after_provider_success_before_local_receipt(tmp_path: Path) -> None:
    class CrashAfterProviderSuccess:
        def __init__(self) -> None:
            self.event_id: str | None = None
            self.calls = 0

        def create_event(self, *, calendar_id, event_id, summary, start, end):
            self.calls += 1
            if self.event_id is None:
                self.event_id = event_id  # Calendar accepted this create.
                raise ConnectionError("Alfred crashed before it received the response")
            assert event_id == self.event_id
            return {"id": event_id}  # Calendar's duplicate-ID recovery lookup.

    database = Database(tmp_path / "alfred.db")
    approvals = ApprovalService(database)
    transport = CrashAfterProviderSuccess()
    proposal = GoogleCalendarActions(database, approvals).propose_event(
        actor="nico",
        calendar_id="primary",
        summary="Advisor meeting",
        start=datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
        end=datetime(2026, 8, 15, 11, 0, tzinfo=UTC),
    )
    issued = approvals.approve(proposal.id, actor="nico")

    with pytest.raises(ConnectionError):
        GoogleCalendarActions(database, approvals, transport).execute(proposal.id, actor="nico", token=issued.token)
    recovered = GoogleCalendarActions(database, approvals, transport).execute(proposal.id, actor="nico", token=issued.token)

    assert recovered.replayed is False
    assert recovered.calendar_event_id == transport.event_id
    assert transport.calls == 2
    assert approvals.get(proposal.id).state == "consumed"


def test_calendar_client_recovers_an_uncertain_create_from_the_stable_event_id() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            assert json.loads(request.content)["id"] == "alfred0123456789abcdef0123456789abcdef"
            return httpx.Response(409, json={"error": {"reason": "duplicate"}})
        return httpx.Response(200, json={"id": "alfred0123456789abcdef0123456789abcdef"})

    client = GoogleCalendarClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        recovered = client.create_event(
            calendar_id="primary",
            event_id="alfred0123456789abcdef0123456789abcdef",
            summary="Advisor meeting",
            start=datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
            end=datetime(2026, 8, 15, 11, 0, tzinfo=UTC),
        )
    finally:
        client.close()
    assert recovered["id"] == "alfred0123456789abcdef0123456789abcdef"
    assert calls == [
        ("POST", "/calendar/v3/calendars/primary/events"),
        ("GET", "/calendar/v3/calendars/primary/events/alfred0123456789abcdef0123456789abcdef"),
    ]
