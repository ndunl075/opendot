from datetime import UTC, datetime
import json
from pathlib import Path

import httpx

from alfred.canvas import CanvasClient, CanvasSync
from alfred.db import Database


class FakeCanvas:
    def list_assignments(self):
        return (
            [
                {
                    "id": 10,
                    "title": "Read chapter 4",
                    "due_at": "2026-08-16T16:00:00Z",
                    "context_name": "Biology",
                    "html_url": "https://school.example/assignments/10",
                    "description": "Do not copy this.",
                }
            ],
            [
                {
                    "id": 9,
                    "name": "Late essay",
                    "due_at": "2026-08-10T16:00:00Z",
                    "course_name": "Writing",
                    "updated_at": "2026-08-11T12:00:00Z",
                }
            ],
        )


def test_canvas_sync_stores_only_assignment_brief_fields(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    first = CanvasSync(database, FakeCanvas()).sync()
    second = CanvasSync(database, FakeCanvas()).sync()

    assert (first.received, first.stored, second.stored) == (2, 2, 0)
    with database.connect() as connection:
        rows = connection.execute("SELECT content, metadata_json FROM events WHERE source = 'canvas' ORDER BY content").fetchall()
        assert [row["content"] for row in rows] == ["Late essay", "Read chapter 4"]
        serialized = " ".join(row["metadata_json"] for row in rows)
        assert "Do not copy" not in serialized
        assert "canvas-api-token" not in serialized
        assert connection.execute("SELECT last_success_at FROM sync_state WHERE connector = 'canvas'").fetchone()[0]


def test_canvas_snapshot_marks_resolved_missing_assignment_inactive(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    CanvasSync(database, FakeCanvas()).sync()

    class ResolvedCanvas:
        def list_assignments(self):
            return ([], [])

    CanvasSync(database, ResolvedCanvas()).sync()
    with database.connect() as connection:
        active = connection.execute(
            "SELECT active FROM connector_records WHERE connector = 'canvas' AND record_type = 'missing' AND record_id = '9'"
        ).fetchone()[0]
    assert active == 0


def test_canvas_client_uses_current_user_read_only_endpoints() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer TOKEN"
        assert request.url.params["per_page"] == "100"
        return httpx.Response(200, json=[])

    client = CanvasClient("https://school.example", "TOKEN", transport=httpx.MockTransport(handler))
    try:
        assert client.list_assignments() == ([], [])
    finally:
        client.close()
    assert seen == ["/api/v1/users/self/upcoming_events", "/api/v1/users/self/missing_submissions"]


def test_canvas_client_reads_historical_assignments_with_course_context() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/v1/courses":
            assert request.url.params["enrollment_state"] in {"active", "completed"}
            if request.url.params["enrollment_state"] == "completed":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"id": 7, "name": "Biology"}])
        assert request.url.params["include[]"] == "submission"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 99,
                    "name": "Final lab",
                    "due_at": "2026-05-01T16:00:00Z",
                    "updated_at": "2026-05-02T16:00:00Z",
                    "submission": {"workflow_state": "graded"},
                }
            ],
        )

    client = CanvasClient("https://school.example", "TOKEN", transport=httpx.MockTransport(handler))
    try:
        items = client.list_historical_assignments()
    finally:
        client.close()

    assert seen == ["/api/v1/courses", "/api/v1/courses", "/api/v1/courses/7/assignments"]
    assert items[0]["course_name"] == "Biology"
    assert items[0]["submission"]["workflow_state"] == "graded"


def test_canvas_sync_persists_historical_submission_state(tmp_path: Path) -> None:
    class HistoricalCanvas(FakeCanvas):
        def list_historical_assignments(self, *, course_limit: int = 100):
            assert course_limit == 100
            return [
                {
                    "id": 99,
                    "name": "Final lab",
                    "due_at": "2026-05-01T16:00:00Z",
                    "updated_at": "2026-05-02T16:00:00Z",
                    "course_name": "Biology",
                    "submission": {"workflow_state": "graded"},
                }
            ]

    database = Database(tmp_path / "alfred.db")
    result = CanvasSync(database, HistoricalCanvas(), include_history=True).sync()

    assert result.historical == 1
    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM connector_records WHERE connector = 'canvas' AND record_type = 'historical'"
        ).fetchone()
    assert json.loads(row["payload_json"])["submission_status"] == "graded"
