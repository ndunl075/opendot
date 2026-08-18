from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from alfred.google_health import (
    DATA_TYPES,
    REQUIRED_SCOPES,
    GoogleHealthClient,
    GoogleHealthSync,
    HealthAccountNotLinked,
    google_auth_scopes,
)
from alfred.google_oauth import DEFAULT_SCOPES
from alfred.db import Database


def _steps_point(point_id: str | None, *, start: str, count: int | str, named: bool = True) -> dict:
    point: dict = {
        "steps": {
            "interval": {"startTime": start, "endTime": start},
            "count": str(count),
        }
    }
    if named and point_id:
        point["name"] = f"users/me/dataTypes/steps/dataPoints/{point_id}"
    return point


def _resting_heart_rate_point(*, year: int, month: int, day: int, bpm: int | str) -> dict:
    return {
        "dailyRestingHeartRate": {
            "date": {"year": year, "month": month, "day": day},
            "beatsPerMinute": str(bpm),
        }
    }


def _sleep_point(point_id: str, *, start: str, end: str, sleep_type: str = "STAGES") -> dict:
    return {
        "name": f"users/me/dataTypes/sleep/dataPoints/{point_id}",
        "sleep": {
            "interval": {"startTime": start, "endTime": end},
            "type": sleep_type,
            "stages": [{"startTime": start, "endTime": end, "type": "DEEP"}],
        },
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
            "daily-resting-heart-rate": [_resting_heart_rate_point(year=2026, month=8, day=10, bpm=62)],
            "sleep": [_sleep_point("sl1", start="2026-08-09T23:00:00Z", end="2026-08-10T06:30:00Z")],
        }
    )

    result = GoogleHealthSync(database, transport).sync()

    assert (result.received, result.stored) == (3, 3)
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT content, sensitivity, metadata_json FROM events WHERE source = 'google_health' ORDER BY content"
        ).fetchall()
    contents = [row["content"] for row in rows]
    assert contents == ["62 bpm resting", "8,432 steps", "sleep: 7h 30m (stages)"]
    assert all(row["sensitivity"] == "sensitive" for row in rows)


def test_health_sync_is_idempotent_across_data_types(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    points = {
        "steps": [_steps_point("s1", start="2026-08-10T00:00:00Z", count=8432)],
        "daily-resting-heart-rate": [],
        "sleep": [],
    }

    first = GoogleHealthSync(database, FakeHealthTransport(points)).sync()
    second = GoogleHealthSync(database, FakeHealthTransport(points)).sync()

    assert (first.received, first.stored) == (1, 1)
    assert (second.received, second.stored) == (1, 0)


def test_health_sync_hashes_an_unnamed_point_into_a_stable_id(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    unnamed = _steps_point(None, start="2026-08-10T00:00:00Z", count=1200, named=False)
    transport = FakeHealthTransport(
        {"steps": [unnamed], "daily-resting-heart-rate": [], "sleep": []}
    )

    first = GoogleHealthSync(database, transport).sync()
    second = GoogleHealthSync(database, FakeHealthTransport(transport.points_by_type)).sync()

    assert (first.received, first.stored) == (1, 1)
    assert (second.received, second.stored) == (1, 0)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT external_id FROM events WHERE source = 'google_health'"
        ).fetchone()
    assert row["external_id"].startswith("steps:")
    assert row["external_id"] != "steps:"


def test_health_sync_snapshot_records_the_full_raw_point_for_provenance(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    point = _steps_point("s1", start="2026-08-10T00:00:00Z", count=8432)
    transport = FakeHealthTransport({"steps": [point], "daily-resting-heart-rate": [], "sleep": []})

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
    transport = FakeHealthTransport(
        {"steps": [{"steps": {"count": 100}}], "daily-resting-heart-rate": [], "sleep": []}
    )

    result = GoogleHealthSync(database, transport).sync()

    assert (result.received, result.stored) == (1, 0)


def test_default_sync_types_are_secretary_density_not_sample_heart_rate() -> None:
    assert DATA_TYPES == ("steps", "sleep", "daily-resting-heart-rate")
    assert "heart-rate" not in DATA_TYPES


def test_health_client_sends_a_typed_time_filter_and_bearer_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v4/users/me/dataTypes/heart-rate/dataPoints"
        assert request.headers["Authorization"] == "Bearer TOKEN"
        assert 'heart_rate.sample_time.physical_time >= "2026-08-01T00:00:00Z"' in request.url.params["filter"]
        assert request.url.params["pageSize"] == "10000"
        return httpx.Response(200, json={"dataPoints": [{"name": "users/me/dataTypes/heart-rate/dataPoints/1"}]})

    client = GoogleHealthClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        points = client.list_data_points(data_type="heart-rate", since=datetime(2026, 8, 1, tzinfo=UTC))
    finally:
        client.close()
    assert len(points) == 1


def test_health_client_caps_sleep_page_size_and_filters_on_end_time() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v4/users/me/dataTypes/sleep/dataPoints"
        assert request.url.params["pageSize"] == "25"
        assert 'sleep.interval.end_time >= "2026-08-01T00:00:00Z"' in request.url.params["filter"]
        return httpx.Response(200, json={})

    client = GoogleHealthClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        assert client.list_data_points(data_type="sleep", since=datetime(2026, 8, 1, tzinfo=UTC)) == []
    finally:
        client.close()


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


def test_health_client_raises_when_data_points_is_not_a_list() -> None:
    client = GoogleHealthClient(
        "TOKEN", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"dataPoints": "nope"}))
    )
    try:
        with pytest.raises(ValueError, match="dataPoints"):
            client.list_data_points(data_type="steps", since=datetime(2026, 8, 1, tzinfo=UTC))
    finally:
        client.close()


def test_health_client_maps_a_403_to_an_operator_action() -> None:
    client = GoogleHealthClient(
        "TOKEN",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                403, json={"error": {"status": "PERMISSION_DENIED", "message": "Request had insufficient authentication scopes."}}
            )
        ),
    )
    try:
        with pytest.raises(PermissionError, match="include-health"):
            client.list_data_points(data_type="steps", since=datetime(2026, 8, 1, tzinfo=UTC))
    finally:
        client.close()


def test_health_client_maps_a_disabled_api_to_enable_not_reauth() -> None:
    client = GoogleHealthClient(
        "TOKEN",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                403,
                json={
                    "error": {
                        "status": "PERMISSION_DENIED",
                        "message": "Google Health API has not been used in project 1 before or it is disabled.",
                        "details": [{"reason": "SERVICE_DISABLED"}],
                    }
                },
            )
        ),
    )
    try:
        with pytest.raises(PermissionError, match="Enable the Google Health API"):
            client.list_data_points(data_type="steps", since=datetime(2026, 8, 1, tzinfo=UTC))
    finally:
        client.close()


def test_health_client_maps_an_unlinked_account_to_a_clear_operator_error() -> None:
    client = GoogleHealthClient(
        "TOKEN",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                400,
                json={
                    "error": {
                        "message": "The account is not linked to Google Health.",
                        "details": [{"reason": "ACCOUNT_NOT_LINKED"}],
                    }
                },
            )
        ),
    )
    try:
        with pytest.raises(HealthAccountNotLinked, match="not linked to Fitbit"):
            client.list_data_points(data_type="steps", since=datetime(2026, 8, 1, tzinfo=UTC))
    finally:
        client.close()


def test_health_client_requires_a_non_empty_token() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        GoogleHealthClient("  ")


def test_google_auth_scopes_keep_health_off_the_default_consent_screen() -> None:
    assert google_auth_scopes() == DEFAULT_SCOPES
    assert not any(scope in DEFAULT_SCOPES for scope in REQUIRED_SCOPES)
    combined = google_auth_scopes(include_health=True)
    assert combined[: len(DEFAULT_SCOPES)] == DEFAULT_SCOPES
    assert set(REQUIRED_SCOPES) <= set(combined)
