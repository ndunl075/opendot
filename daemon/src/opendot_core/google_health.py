"""Read-only Google Health sync: steps, sleep, and daily resting heart rate.

Decision 7 targets the Google Health API specifically, not the legacy
Fitbit Web API (which the doc notes stops syncing in September 2026).
Section 8's permissions table disables health *writes* in v1; this
connector never attempts one. Every stored value is tagged `sensitive`,
matching section 8's "Tag data public, personal, sensitive, or secret"
rule, so MCP client scopes and vault export both keep it out of anywhere
it isn't explicitly granted.

The v4 REST shapes come from developers.google.com/health. A DataPoint
puts the typed payload (and its interval / sample time / civil date) in
the union field -- `steps`, `sleep`, `heartRate`, `dailyRestingHeartRate`
-- not at the top level. Most types are not identifiable, so `name` is
often empty; sleep sessions are the exception. Sample `heart-rate` is
supported by the client but is not part of the default sync: a 14-day
lookback of wearable samples is too dense for the event log, and daily
resting heart rate is the secretary-useful metric.

`_normalize_data_point` still keeps the complete raw point in
`metadata["raw"]` so a remaining field-name mismatch cannot silently drop
the observation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .connector_records import ConnectorRecordStore
from .db import Database
from .events import EventStore
from .google_oauth import DEFAULT_SCOPES
from .wall_clock import format_duration

# Additional OAuth scopes beyond google_oauth.DEFAULT_SCOPES. Health access
# is never silently bundled into the Calendar/Gmail consent screen; pass
# `alfred google-auth --include-health` (or repeated `--scope` flags) so the
# operator opts in on a distinct consent screen.
SLEEP_SCOPE = "https://www.googleapis.com/auth/googlehealth.sleep.readonly"
ACTIVITY_SCOPE = "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"
VITALS_SCOPE = "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly"
REQUIRED_SCOPES: tuple[str, ...] = (SLEEP_SCOPE, ACTIVITY_SCOPE, VITALS_SCOPE)


class HealthAccountNotLinked(RuntimeError):
    """The Google account has OAuth, but no Fitbit/Google Health link."""


DataTypeName = Literal["steps", "sleep", "heart-rate", "daily-resting-heart-rate"]
DATA_TYPES: tuple[DataTypeName, ...] = ("steps", "sleep", "daily-resting-heart-rate")

# URL path segment -> union field, AIP-160 filter field, and list page size.
# Sleep/exercise cap at 25; everything else truncates above 10_000.
_PAYLOAD_FIELD: dict[str, str] = {
    "steps": "steps",
    "sleep": "sleep",
    "heart-rate": "heartRate",
    "daily-resting-heart-rate": "dailyRestingHeartRate",
}
_FILTER_FIELD: dict[str, str] = {
    "steps": "steps.interval.start_time",
    "sleep": "sleep.interval.end_time",
    "heart-rate": "heart_rate.sample_time.physical_time",
    "daily-resting-heart-rate": "daily_resting_heart_rate.date",
}
_PAGE_SIZE: dict[str, int] = {
    "steps": 10_000,
    "sleep": 25,
    "heart-rate": 10_000,
    "daily-resting-heart-rate": 90,
}


def google_auth_scopes(*, include_health: bool = False, requested: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Calendar/Gmail defaults, plus health scopes only when the operator asked."""
    scopes = requested or DEFAULT_SCOPES
    if include_health:
        scopes = tuple(dict.fromkeys((*scopes, *REQUIRED_SCOPES)))
    return scopes


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
            "filter": _list_filter(data_type, since),
            "pageSize": _PAGE_SIZE.get(data_type, 1_440),
        }
        items: list[dict[str, Any]] = []
        for _ in range(50):
            response = self._client.get(f"/users/me/dataTypes/{data_type}/dataPoints", params=params)
            if response.status_code in {401, 403}:
                raise PermissionError(_denied_message(response))
            if response.status_code == 400:
                raise _bad_request_error(data_type, response)
            response.raise_for_status()
            payload = response.json()
            page_items = payload.get("dataPoints", [])
            if page_items is None:
                page_items = []
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
    """Snapshot the last `lookback_days` of steps/sleep/resting-HR as source events."""

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


def _error_info(response: httpx.Response) -> tuple[str, str, str]:
    """Return (status, reason, first-clause message). Never a data payload."""
    status = ""
    reason = ""
    message = ""
    try:
        error = response.json().get("error") or {}
        if isinstance(error, dict):
            status = str(error.get("status") or "")
            message = str(error.get("message") or "").split(".")[0][:160]
            details = error.get("details")
            if isinstance(details, list) and details and isinstance(details[0], dict):
                reason = str(details[0].get("reason") or "")
    except ValueError:
        pass
    return status, reason, message


def _denied_message(response: httpx.Response) -> str:
    """Operator-facing 401/403 text: Google's status/reason, never a payload."""
    status, reason, message = _error_info(response)
    hint = "Enable the Google Health API, add the three googlehealth.*.readonly scopes on the OAuth Data Access page, and re-run `alfred google-auth --include-health`."
    if reason == "SERVICE_DISABLED":
        hint = "Enable the Google Health API on this Cloud project, then retry `alfred health-sync`."
    elif reason == "DISALLOWED_OAUTH_SCOPES":
        hint = (
            "Google Health rejects Calendar/Gmail scopes on the same access token. "
            "Alfred downscopes the refresh; if this persists, re-run `alfred google-auth --include-health`."
        )
    elif "insufficient authentication scopes" in message.lower():
        hint = "Re-run `alfred google-auth --include-health` so the refresh token includes the health scopes."
    detail = ", ".join(part for part in (status, reason) if part)
    return f"Google Health denied the request ({detail or response.status_code}). {hint}"


def _bad_request_error(data_type: DataTypeName, response: httpx.Response) -> Exception:
    _, reason, message = _error_info(response)
    if reason == "ACCOUNT_NOT_LINKED":
        return HealthAccountNotLinked(
            "This Google account is not linked to Fitbit/Google Health. "
            "Sign into Fitbit with the same Google account, then retry `alfred health-sync`."
        )
    detail = reason or message or str(response.status_code)
    return ValueError(f"Google Health rejected the {data_type} query ({detail})")


def _list_filter(data_type: DataTypeName, since: datetime) -> str:
    field = _FILTER_FIELD.get(data_type)
    if field is None:
        raise ValueError(f"unsupported Google Health data type: {data_type}")
    if data_type == "daily-resting-heart-rate":
        return f'{field} >= "{since.astimezone(UTC).date().isoformat()}"'
    return f'{field} >= "{_rfc3339(since)}"'


def _typed_payload(data_type: DataTypeName, point: dict[str, Any]) -> dict[str, Any]:
    field = _PAYLOAD_FIELD.get(data_type)
    nested = point.get(field) if field else None
    return nested if isinstance(nested, dict) else {}


def _point_id(data_type: DataTypeName, point: dict[str, Any]) -> str | None:
    """Prefer the resource name; otherwise a stable hash of the typed payload.

    Most Health data types are not identifiable -- `name` is empty -- so a
    hash of the typed observation is the only idempotent key we can form.
    """
    name = point.get("name")
    if isinstance(name, str) and name.strip():
        return name.rsplit("/", maxsplit=1)[-1]
    payload = _typed_payload(data_type, point)
    if not payload:
        return None
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return digest[:32]


def _civil_date(value: object) -> date | None:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    if isinstance(value, dict):
        year, month, day = value.get("year"), value.get("month"), value.get("day")
        if isinstance(year, int) and isinstance(month, int) and isinstance(day, int):
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def _point_time(data_type: DataTypeName, point: dict[str, Any]) -> datetime | None:
    """Best-effort timestamp: typed interval/sample/date, then a top-level fallback."""
    payload = _typed_payload(data_type, point)
    candidates = (payload, point)
    for source in candidates:
        interval = source.get("interval")
        if isinstance(interval, dict):
            start = interval.get("startTime")
            if isinstance(start, str):
                parsed = _parse_rfc3339(start)
                if parsed is not None:
                    return parsed
        sample_time = source.get("sampleTime")
        if isinstance(sample_time, dict):
            physical_time = sample_time.get("physicalTime")
            if isinstance(physical_time, str):
                parsed = _parse_rfc3339(physical_time)
                if parsed is not None:
                    return parsed
        civil = _civil_date(source.get("date"))
        if civil is not None:
            return datetime(civil.year, civil.month, civil.day, tzinfo=UTC)
        for key in ("updateTime", "createTime"):
            value = source.get(key)
            if isinstance(value, str):
                parsed = _parse_rfc3339(value)
                if parsed is not None:
                    return parsed
    return None


def _as_number(value: object) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value) if value.lstrip("-").isdigit() else float(value)
        except ValueError:
            return None
    return None


def _sleep_interval(payload: dict[str, Any]) -> tuple[datetime, datetime] | None:
    interval = payload.get("interval")
    if not isinstance(interval, dict):
        return None
    start = _parse_rfc3339(interval["startTime"]) if isinstance(interval.get("startTime"), str) else None
    end = _parse_rfc3339(interval["endTime"]) if isinstance(interval.get("endTime"), str) else None
    if start is None or end is None or end <= start:
        return None
    return start, end


def _point_summary(data_type: DataTypeName, point: dict[str, Any]) -> str:
    """Best-effort one-line summary; the full point is always kept in metadata too."""
    payload = _typed_payload(data_type, point)
    if data_type == "steps":
        count = _as_number(payload.get("count") if payload else None)
        if count is None:
            count = _as_number(point.get("count"))
            steps = point.get("steps")
            if count is None and isinstance(steps, dict):
                count = _as_number(steps.get("count"))
        if count is not None:
            return f"{int(count):,} steps"
    if data_type in {"heart-rate", "daily-resting-heart-rate"}:
        bpm = _as_number(payload.get("beatsPerMinute") if payload else None)
        if bpm is None:
            heart_rate = point.get("heartRate")
            bpm = heart_rate.get("beatsPerMinute") if isinstance(heart_rate, dict) else point.get("beatsPerMinute")
            bpm = _as_number(bpm)
        if bpm is not None:
            label = " bpm resting" if data_type == "daily-resting-heart-rate" else " bpm"
            return f"{bpm:g}{label}"
    if data_type == "sleep":
        sleep = payload or (point.get("sleep") if isinstance(point.get("sleep"), dict) else {})
        bounds = _sleep_interval(sleep) or _sleep_interval(point)
        sleep_type = sleep.get("type") if isinstance(sleep.get("type"), str) else None
        if bounds is not None:
            summary = f"sleep: {format_duration(bounds[1] - bounds[0])}"
            if sleep_type:
                return f"{summary} ({sleep_type.lower()})"
            return summary
        stage = sleep.get("stage") if isinstance(sleep, dict) else point.get("stage")
        if isinstance(stage, str) and stage.strip():
            return f"sleep: {stage.strip().lower()}"
        if sleep_type:
            return f"sleep: {sleep_type.lower()}"
    return f"{data_type} data point"


def _normalize_data_point(data_type: DataTypeName, point: dict[str, Any]) -> dict[str, Any] | None:
    point_id = _point_id(data_type, point)
    occurred_at = _point_time(data_type, point)
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
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_rfc3339(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
