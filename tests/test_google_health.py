from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from alfred.db import Database
from alfred.google_health import GoogleHealthClient, GoogleHealthSync


def _steps_point(point_id: str, *, start: str, count: int) -> dict:
    return {
        "name": f"users/me/dataTypes/steps/dataPoints/{point_id}",
        "interval": {"startTime": start, "endTime": start},
        "steps": {"count": count},
    }


def _heart_rate_point(point_id: str, *, sample_time: str, bpm: int) -> dict:
    return {
        "name": f"users/me/dataTypes/heart-rate/dataPoints/{point_id}",
        "sampleTime": {"physicalTime": sample_time},
        "heartRate": {"beatsPerMinute": bpm},
    }


def _sleep_point(point_id: str, *, start: str, stage: str) -> dict:
    return {
        "name": f"users/me/dataTypes/sleep/dataPoints/{point_id}",
        "interval": {"startTime": start, "endTime": start},
        "sleep": {"stage": stage},
    }


class FakeHealthTransport:
    def __init__(self, points_by_type: dict[str, list[dict]]) -> None:
        self.points_by_type = points_by_type
        self.calls: list[str] = []

    def list_data_points(self, *, data_type, since):
        self.calls.append(data_type)
        return self.points_by_type.get(data_type, [])


def test_health_sync_stores_a_sensitive_event_per_data_point(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    transport = FakeHealthTransport(
        {
            "steps": [_steps_point("s1", start="2026-08-10T00:00:00Z", count=8432)],
            "heart-rate": [_heart_rate_point("h1", sample_time="2026-08-10T08:00:00Z", bpm=62)],
            "sleep": [_sleep_point("sl1", start="2026-08-09T23:00:00Z", stage="deep")],
        }
    )

    result = GoogleHealthSync(database, transport).sync()

    assert (result.received, result.stored) == (3, 3)
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT content, sensitivity, metadata_json FROM events WHERE source = 'google_health' ORDER BY content"
        ).fetchall()
    contents = [row["content"] for row in rows]
    assert contents == ["62 bpm", "8,432 steps", "sleep: deep"]
    assert all(row["sensitivity"] == "sensitive" for row in rows)


def test_health_sync_is_idempotent_across_data_types(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    points = {
        "steps": [_steps_point("s1", start="2026-08-10T00:00:00Z", count=8432)],
        "heart-rate": [],
        "sleep": [],
    }

    first = GoogleHealthSync(database, FakeHealthTransport(points)).sync()
    second = GoogleHealthSync(database, FakeHealthTransport(points)).sync()

    assert (first.received, first.stored) == (1, 1)
    assert (second.received, second.stored) == (1, 0)


def test_health_sync_snapshot_records_the_full_raw_point_for_provenance(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    point = _steps_point("s1", start="2026-08-10T00:00:00Z", count=8432)
    transport = FakeHealthTransport({"steps": [point], "heart-rate": [], "sleep": []})

    GoogleHealthSync(database, transport).sync()

    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM connector_records WHERE connector = 'google_health' AND record_type = 'steps'"
        ).fetchone()
    assert row is not None
    assert "8432" in row["payload_json"]


def test_health_sync_records_an_error_without_raising_being_swallowed(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")

    class FailingTransport:
        def list_data_points(self, *, data_type, since):
            raise ConnectionError("network down")

    with pytest.raises(ConnectionError):
        GoogleHealthSync(database, FailingTransport()).sync()

    with database.connect() as connection:
        row = connection.execute("SELECT last_error FROM sync_state WHERE connector = 'google_health'").fetchone()
    assert row["last_error"] == "ConnectionError"


def test_health_sync_rejects_a_non_positive_lookback(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        GoogleHealthSync(Database(tmp_path / "alfred.db"), FakeHealthTransport({}), lookback_days=0)


def test_normalize_skips_a_point_with_no_recognizable_id_or_time(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    transport = FakeHealthTransport({"steps": [{"steps": {"count": 100}}], "heart-rate": [], "sleep": []})

    result = GoogleHealthSync(database, transport).sync()

    assert (result.received, result.stored) == (1, 0)


def test_health_client_sends_a_time_filter_and_bearer_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v4/users/me/dataTypes/heart-rate/dataPoints"
        assert request.headers["Authorization"] == "Bearer TOKEN"
        assert 'interval.start_time >= "2026-08-01T00:00:00Z"' in request.url.params["filter"]
        return httpx.Response(200, json={"dataPoints": [{"name": "users/me/dataTypes/heart-rate/dataPoints/1"}]})

    client = GoogleHealthClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        points = client.list_data_points(data_type="heart-rate", since=datetime(2026, 8, 1, tzinfo=UTC))
    finally:
        client.close()
    assert len(points) == 1


def test_health_client_paginates_through_next_page_token() -> None:
    pages = [
        {"dataPoints": [{"name": "users/me/dataTypes/steps/dataPoints/1"}], "nextPageToken": "page2"},
        {"dataPoints": [{"name": "users/me/dataTypes/steps/dataPoints/2"}]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages.pop(0))

    client = GoogleHealthClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        points = client.list_data_points(data_type="steps", since=datetime(2026, 8, 1, tzinfo=UTC))
    finally:
        client.close()
    assert [point["name"] for point in points] == [
        "users/me/dataTypes/steps/dataPoints/1",
        "users/me/dataTypes/steps/dataPoints/2",
    ]


def test_health_client_raises_when_the_response_has_no_data_points_list() -> None:
    client = GoogleHealthClient("TOKEN", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    try:
        with pytest.raises(ValueError, match="dataPoints"):
            client.list_data_points(data_type="steps", since=datetime(2026, 8, 1, tzinfo=UTC))
    finally:
        client.close()


def test_health_client_requires_a_non_empty_token() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        GoogleHealthClient("  ")
