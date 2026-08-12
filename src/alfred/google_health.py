"""Read-only Google Health sync: steps, sleep, and heart rate.

Decision 7 targets the Google Health API specifically, not the legacy
Fitbit Web API (which the doc notes stops syncing in September 2026).
Section 8's permissions table disables health *writes* in v1; this
connector never attempts one. Every stored value is tagged `sensitive`,
matching section 8's "Tag data public, personal, sensitive, or secret"
rule, so MCP client scopes and vault export both keep it out of anywhere
it isn't explicitly granted.

Endpoint shapes, scopes, and field names below are sourced from Google's
own v4 REST reference (developers.google.com/health) as of 2026-08 -- this
project has no way to smoke-test against a real Google Health-linked
wearable account. Like Slack Socket Mode, treat this as *built but
unverified* until it has actually run against one; `_normalize_data_point`
is deliberately defensive (it keeps the complete raw data point in
`metadata["raw"]` alongside its best-effort summary) precisely because a
field name here could be wrong and nothing should be silently lost if so.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .connector_records import ConnectorRecordStore
from .db import Database
from .events import EventStore

# Additional OAuth scopes beyond google_oauth.DEFAULT_SCOPES. Pass them
# explicitly with repeated `alfred google-auth --scope` flags alongside the
# defaults -- section 12 already treats the default grant as overridable/
# extendable per operator, and health access should never be silently
# bundled into the Calendar/Gmail consent screen everyone else gets.
SLEEP_SCOPE = "https://www.googleapis.com/auth/googlehealth.sleep.readonly"
ACTIVITY_SCOPE = "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"
VITALS_SCOPE = "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly"
REQUIRED_SCOPES: tuple[str, ...] = (SLEEP_SCOPE, ACTIVITY_SCOPE, VITALS_SCOPE)

DataTypeName = Literal["steps", "sleep", "heart-rate"]
DATA_TYPES: tuple[DataTypeName, ...] = ("steps", "sleep", "heart-rate")


class HealthTransport(Protocol):
    def list_data_points(self, *, data_type: DataTypeName, since: datetime) -> list[dict[str, Any]]: ...


class GoogleHealthClient:
    """Client for the Google Health API's read-only dataPoints endpoint."""

    api_base = "https://health.googleapis.com/v4"

    def __init__(self, access_token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        if not access_token.strip():
            raise ValueError("Google Health access token must not be empty")
        self._client = httpx.Client(
            base_url=self.api_base,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=httpx.Timeout(30.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def list_data_points(self, *, data_type: DataTypeName, since: datetime) -> list[dict[str, Any]]:
        """Return every data point of one type at/after `since`, paginating as needed."""
        params: dict[str, str | int] = {
            "filter": f'interval.start_time >= "{_rfc3339(since)}"',
            "pageSize": 10_000,
        }
        items: list[dict[str, Any]] = []
        for _ in range(50):
            response = self._client.get(f"/users/me/dataTypes/{data_type}/dataPoints", params=params)
            response.raise_for_status()
            payload = response.json()
            page_items = payload.get("dataPoints")
            if not isinstance(page_items, list):
                raise ValueError(f"Google Health {data_type} response has no 'dataPoints' list")
            items.extend(item for item in page_items if isinstance(item, dict))
            next_token = payload.get("nextPageToken")
            if not next_token:
                return items
            if not isinstance(next_token, str):
                raise ValueError(f"Google Health {data_type} response has an invalid nextPageToken")
            params["pageToken"] = next_token
        raise ValueError(f"Google Health {data_type} pagination exceeded 50 pages")


class HealthSyncResult(BaseModel):
    received: int
    stored: int


class GoogleHealthSync:
    """Snapshot the last `lookback_days` of steps/sleep/heart-rate as source events."""

    connector_name = "google_health"
    account_name = "self"

    def __init__(self, database: Database, transport: HealthTransport, *, lookback_days: int = 14) -> None:
        if lookback_days <= 0:
            raise ValueError("lookback_days must be positive")
        self.database = database
        self.transport = transport
        self.lookback_days = lookback_days

    def sync(self) -> HealthSyncResult:
        self.database.migrate()
        since = datetime.now(UTC) - timedelta(days=self.lookback_days)
        try:
            points_by_type = {
                data_type: self.transport.list_data_points(data_type=data_type, since=since) for data_type in DATA_TYPES
            }
        except Exception as error:
            self._store_error(error.__class__.__name__)
            raise
        stored = 0
        received = 0
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                for data_type, points in points_by_type.items():
                    received += len(points)
                    current_records: dict[str, dict[str, Any]] = {}
                    for point in points:
                        event = _normalize_data_point(data_type, point)
                        if event is None:
                            continue
                        if EventStore.append(connection, **event).is_new:
                            stored += 1
                        current_records[event["external_id"]] = event["metadata"]
                    ConnectorRecordStore.replace_snapshot(
                        connection,
                        connector=self.connector_name,
                        account=self.account_name,
                        record_type=data_type,
                        records=current_records,
                    )
                self._store_success_in_transaction(connection)
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="system:google_health",
                        client="google_health",
                        tool="google_health_read_sync",
                        outcome="ok",
                        result={"received": received, "stored": stored},
                    ),
                )
        return HealthSyncResult(received=received, stored=stored)

    def _store_success_in_transaction(self, connection: Any) -> None:
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at)
            VALUES (?, ?, NULL, ?, NULL, ?)
            ON CONFLICT(connector, account) DO UPDATE SET
                last_success_at = excluded.last_success_at, last_error = NULL, updated_at = excluded.updated_at
            """,
            (self.connector_name, self.account_name, now, now),
        )

    def _store_error(self, reason: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at)
                    VALUES (?, ?, NULL, NULL, ?, ?)
                    ON CONFLICT(connector, account) DO UPDATE SET last_error = excluded.last_error, updated_at = excluded.updated_at
                    """,
                    (self.connector_name, self.account_name, reason, now),
                )


def _point_id(point: dict[str, Any]) -> str | None:
    """Extract the trailing dataPoint ID from a resource `name` like
    'users/me/dataTypes/steps/dataPoints/{id}'."""
    name = point.get("name")
    if isinstance(name, str) and name:
        return name.rsplit("/", maxsplit=1)[-1]
    return None


def _point_time(point: dict[str, Any]) -> datetime | None:
    """Best-effort timestamp: an interval's start, a sample's own time, or the update time."""
    interval = point.get("interval")
    if isinstance(interval, dict):
        start = interval.get("startTime")
        if isinstance(start, str):
            return _parse_rfc3339(start)
    sample_time = point.get("sampleTime")
    if isinstance(sample_time, dict):
        physical_time = sample_time.get("physicalTime")
        if isinstance(physical_time, str):
            return _parse_rfc3339(physical_time)
    for key in ("updateTime", "createTime"):
        value = point.get(key)
        if isinstance(value, str):
            return _parse_rfc3339(value)
    return None


def _point_summary(data_type: DataTypeName, point: dict[str, Any]) -> str:
    """Best-effort one-line summary; the full point is always kept in metadata too."""
    if data_type == "steps":
        steps = point.get("steps")
        count = steps.get("count") if isinstance(steps, dict) else point.get("count")
        if isinstance(count, int | float):
            return f"{int(count):,} steps"
    if data_type == "heart-rate":
        heart_rate = point.get("heartRate")
        bpm = heart_rate.get("beatsPerMinute") if isinstance(heart_rate, dict) else point.get("beatsPerMinute")
        if isinstance(bpm, int | float):
            return f"{bpm:g} bpm"
    if data_type == "sleep":
        sleep = point.get("sleep")
        stage = sleep.get("stage") if isinstance(sleep, dict) else point.get("stage")
        if isinstance(stage, str):
            return f"sleep: {stage}"
    return f"{data_type} data point"


def _normalize_data_point(data_type: DataTypeName, point: dict[str, Any]) -> dict[str, Any] | None:
    point_id = _point_id(point)
    occurred_at = _point_time(point)
    if point_id is None or occurred_at is None:
        # Can't form a stable external_id or a timestamp -- skip rather than
        # guess; the caller's received count still reflects it was seen.
        return None
    return {
        "source": "google_health",
        "external_id": f"{data_type}:{point_id}",
        "occurred_at": occurred_at,
        "content": _point_summary(data_type, point),
        "metadata": {"data_type": data_type, "raw": point},
        "sensitivity": "sensitive",
    }


def _rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_rfc3339(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
