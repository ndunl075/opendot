import json
from pathlib import Path

import httpx
import pytest

from alfred.academic_memory import AcademicMemoryService
from alfred.canvas_ical import (
    CanvasICalClient,
    CanvasICalError,
    CanvasICalResponse,
    CanvasICalSync,
    parse_canvas_ical,
)
from alfred.db import Database


FEED = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:assignment-998@example.invalid
DTSTAMP:20260813T180000Z
DTSTART;TZID=America/New_York:20260818T235900
SUMMARY:Project 1 [CSE 2231]
DESCRIPTION:Private assignment body that Alfred must never store
URL:https://osu.instructure.com/courses/123/assignments/998?token=also-private
CATEGORIES:CSE 2231
SEQUENCE:4
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""


class FeedTransport:
    def __init__(self, response: CanvasICalResponse | None) -> None:
        self.response = response
        self.calls: list[tuple[str | None, str | None]] = []

    def fetch(self, *, etag: str | None, last_modified: str | None):
        self.calls.append((etag, last_modified))
        return self.response


def test_client_uses_conditional_headers_and_never_exposes_the_feed_url() -> None:
    secret_url = "https://osu.instructure.com/feeds/calendars/user_super-secret.ics"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == secret_url
        assert request.headers["if-none-match"] == '"v1"'
        assert request.headers["if-modified-since"] == "Thu, 13 Aug 2026 18:00:00 GMT"
        return httpx.Response(403, request=request)

    client = CanvasICalClient(secret_url, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(CanvasICalError) as caught:
            client.fetch(etag='"v1"', last_modified="Thu, 13 Aug 2026 18:00:00 GMT")
    finally:
        client.close()

    assert secret_url not in str(caught.value)
    assert "super-secret" not in str(caught.value)


def test_parser_minimizes_canvas_event_and_strips_link_queries() -> None:
    item = parse_canvas_ical(FEED)[0]

    assert item["title"] == "Project 1 [CSE 2231]"
    assert item["due_at"] == "2026-08-18T23:59:00-04:00"
    assert item["course_name"] == "CSE 2231"
    assert item["course_id"] == "123"
    assert item["assignment_id"] == "998"
    assert item["html_url"] == "https://osu.instructure.com/courses/123/assignments/998"
    assert "Private assignment body" not in json.dumps(item, default=str)


def test_parser_rejects_a_login_page_and_preserves_all_day_due_dates() -> None:
    with pytest.raises(CanvasICalError):
        parse_canvas_ical("<html>Please sign in</html>")

    all_day = FEED.replace(
        "DTSTART;TZID=America/New_York:20260818T235900", "DTSTART;VALUE=DATE:20260818"
    )
    assert parse_canvas_ical(all_day)[0]["due_at"] == "2026-08-18T23:59:00+00:00"


def test_sync_persists_minimized_evidence_and_builds_academic_memory(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    transport = FeedTransport(CanvasICalResponse(FEED, '"feed-v1"', "last-modified-v1"))

    result = CanvasICalSync(database, transport).sync()
    rollup = AcademicMemoryService(database).rebuild_if_changed()

    assert result.received == result.stored == result.active == 1
    assert rollup.changed and rollup.items == 1
    with database.connect() as connection:
        event = connection.execute(
            "SELECT source, external_id, content, metadata_json FROM events WHERE source = 'canvas'"
        ).fetchone()
        record = connection.execute(
            """
            SELECT payload_json FROM connector_records
            WHERE connector = 'canvas_ical' AND account = 'self'
              AND record_type = 'assignment' AND active = 1
            """
        ).fetchone()
        state = connection.execute(
            "SELECT cursor, last_error FROM sync_state WHERE connector = 'canvas_ical' AND account = 'self'"
        ).fetchone()

    metadata = json.loads(event["metadata_json"])
    payload = json.loads(record["payload_json"])
    assert event["content"] == payload["title"] == "Project 1 [CSE 2231]"
    assert metadata["source_connector"] == "canvas_ical"
    serialized = json.dumps({"event": dict(event), "record": payload, "state": dict(state)})
    assert "Private assignment body" not in serialized
    assert "token=also-private" not in serialized
    assert json.loads(state["cursor"])["etag"] == '"feed-v1"'
    assert state["last_error"] is None

    recalled = AcademicMemoryService(database).search("CSE 2231 project")
    assert recalled.days[0]["items"][0]["source_event_id"]


def test_not_modified_keeps_the_snapshot_and_refreshes_health(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    CanvasICalSync(
        database, FeedTransport(CanvasICalResponse(FEED, '"feed-v1"', "last-modified-v1"))
    ).sync()
    transport = FeedTransport(None)

    result = CanvasICalSync(database, transport).sync()

    assert result.unchanged and result.active == 1
    assert transport.calls == [('"feed-v1"', "last-modified-v1")]
    with database.connect() as connection:
        active = connection.execute(
            "SELECT COUNT(*) FROM connector_records WHERE connector = 'canvas_ical' AND active = 1"
        ).fetchone()[0]
    assert active == 1


def test_cancelled_revision_retires_the_current_assignment(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    CanvasICalSync(database, FeedTransport(CanvasICalResponse(FEED, None, None))).sync()
    cancelled = FEED.replace("SEQUENCE:4", "SEQUENCE:5").replace("STATUS:CONFIRMED", "STATUS:CANCELLED")

    result = CanvasICalSync(
        database, FeedTransport(CanvasICalResponse(cancelled, None, None))
    ).sync()
    rollup = AcademicMemoryService(database).rebuild_if_changed()

    assert result.stored == 1 and result.active == 0
    assert rollup.items == 0
    with database.connect() as connection:
        active = connection.execute(
            "SELECT COUNT(*) FROM connector_records WHERE connector = 'canvas_ical' AND active = 1"
        ).fetchone()[0]
    assert active == 0


def test_parse_failure_records_only_a_safe_error_class(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    secret = "https://osu.instructure.com/feeds/calendars/private-value.ics"

    class Broken:
        def fetch(self, *, etag, last_modified):
            raise httpx.ConnectError(f"failed to fetch {secret}")

    with pytest.raises(CanvasICalError) as caught:
        CanvasICalSync(database, Broken()).sync()

    assert secret not in str(caught.value)
    with database.connect() as connection:
        error = connection.execute(
            "SELECT last_error FROM sync_state WHERE connector = 'canvas_ical' AND account = 'self'"
        ).fetchone()[0]
    assert error == "ConnectError"
