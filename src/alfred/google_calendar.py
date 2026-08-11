"""Read-only Google Calendar sync with local, privacy-minimizing storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .connector_records import ConnectorRecordStore
from .db import Database
from .events import EventStore


class CalendarTransport(Protocol):
    def list_events(
        self,
        *,
        calendar_id: str,
        sync_token: str | None,
        time_min: datetime | None,
        time_max: datetime | None,
    ) -> tuple[list[dict[str, Any]], str | None]: ...


class SyncTokenExpired(RuntimeError):
    """Google invalidated an incremental-sync cursor; a full sync is required."""


class GoogleCalendarClient:
    """Small REST client for the read-only Calendar events endpoint."""

    api_base = "https://www.googleapis.com/calendar/v3"

    def __init__(
        self, access_token: str, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not access_token.strip():
            raise ValueError("Google Calendar access token must not be empty")
        self._access_token = access_token
        self._client = httpx.Client(
            base_url=self.api_base,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=httpx.Timeout(30.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def list_events(
        self,
        *,
        calendar_id: str,
        sync_token: str | None,
        time_min: datetime | None,
        time_max: datetime | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Return all matching event pages and the final incremental-sync token."""
        params: dict[str, str | bool | int] = {
            "singleEvents": True,
            "showDeleted": True,
            "maxResults": 2500,
        }
        if sync_token:
            params["syncToken"] = sync_token
        else:
            params["orderBy"] = "startTime"
            if time_min:
                params["timeMin"] = _rfc3339(time_min)
            if time_max:
                params["timeMax"] = _rfc3339(time_max)

        items: list[dict[str, Any]] = []
        next_sync_token: str | None = None
        while True:
            response = self._client.get(f"/calendars/{calendar_id}/events", params=params)
            if response.status_code == 410 and sync_token:
                raise SyncTokenExpired("Google Calendar sync token expired")
            response.raise_for_status()
            payload = response.json()
            page_items = payload.get("items", [])
            if not isinstance(page_items, list):
                raise ValueError("Google Calendar response has invalid items")
            items.extend(item for item in page_items if isinstance(item, dict))
            page_token = payload.get("nextPageToken")
            if not page_token:
                final_token = payload.get("nextSyncToken")
                next_sync_token = final_token if isinstance(final_token, str) else None
                return items, next_sync_token
            if not isinstance(page_token, str):
                raise ValueError("Google Calendar response has invalid nextPageToken")
            params["pageToken"] = page_token


class CalendarSyncResult(BaseModel):
    calendar_id: str
    received: int
    stored: int
    reset_cursor: bool = False
    next_cursor: str | None = None


class GoogleCalendarSync:
    """Sync a calendar into immutable source events without copying event bodies."""

    connector_name = "google_calendar"

    def __init__(self, database: Database, transport: CalendarTransport) -> None:
        self.database = database
        self.transport = transport

    def sync(
        self,
        *,
        calendar_id: str = "primary",
        time_min: datetime | None = None,
        time_max: datetime | None = None,
    ) -> CalendarSyncResult:
        self.database.migrate()
        cursor = self._load_cursor(calendar_id)
        reset_cursor = False
        try:
            items, next_cursor = self.transport.list_events(
                calendar_id=calendar_id,
                sync_token=cursor,
                time_min=time_min,
                time_max=time_max,
            )
        except SyncTokenExpired:
            reset_cursor = True
            items, next_cursor = self.transport.list_events(
                calendar_id=calendar_id,
                sync_token=None,
                time_min=time_min,
                time_max=time_max,
            )
        except Exception as error:
            self._store_error(calendar_id, error.__class__.__name__)
            raise

        stored = 0
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                current_records: dict[str, dict[str, Any]] = {}
                for item in items:
                    event = _normalize_event(item, calendar_id)
                    if EventStore.append(connection, **event).is_new:
                        stored += 1
                    metadata = event["metadata"]
                    current_records[metadata["calendar_event_id"]] = {
                        "title": event["content"],
                        "start": metadata["start"],
                        "end": metadata["end"],
                        "html_url": metadata["html_link"],
                    }
                if cursor is None or reset_cursor:
                    ConnectorRecordStore.replace_snapshot(
                        connection,
                        connector=self.connector_name,
                        account=calendar_id,
                        record_type="event",
                        records={
                            record_id: payload
                            for record_id, payload in current_records.items()
                            if _event_status(items, record_id) != "cancelled"
                        },
                    )
                else:
                    for record_id, payload in current_records.items():
                        ConnectorRecordStore.upsert(
                            connection,
                            connector=self.connector_name,
                            account=calendar_id,
                            record_type="event",
                            record_id=record_id,
                            payload=payload,
                            active=_event_status(items, record_id) != "cancelled",
                        )
                self._store_success_in_transaction(connection, calendar_id, next_cursor)
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="system:google_calendar",
                        client="google_calendar",
                        tool="calendar_read_sync",
                        outcome="ok",
                        arguments={"calendar_id": calendar_id},
                        result={"received": len(items), "stored": stored, "cursor_reset": reset_cursor},
                    ),
                )
        return CalendarSyncResult(
            calendar_id=calendar_id,
            received=len(items),
            stored=stored,
            reset_cursor=reset_cursor,
            next_cursor=next_cursor,
        )

    def _load_cursor(self, calendar_id: str) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT cursor FROM sync_state WHERE connector = ? AND account = ?",
                (self.connector_name, calendar_id),
            ).fetchone()
        return str(row["cursor"]) if row and row["cursor"] else None

    def _store_success_in_transaction(self, connection: Any, calendar_id: str, cursor: str | None) -> None:
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at)
            VALUES (?, ?, ?, ?, NULL, ?)
            ON CONFLICT(connector, account) DO UPDATE SET
                cursor = excluded.cursor,
                last_success_at = excluded.last_success_at,
                last_error = NULL,
                updated_at = excluded.updated_at
            """,
            (self.connector_name, calendar_id, cursor, now, now),
        )

    def _store_error(self, calendar_id: str, reason: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at)
                    VALUES (?, ?, NULL, NULL, ?, ?)
                    ON CONFLICT(connector, account) DO UPDATE SET
                        last_error = excluded.last_error,
                        updated_at = excluded.updated_at
                    """,
                    (self.connector_name, calendar_id, reason, now),
                )


def default_sync_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Keep the initial private sync bounded to a useful two-week window."""
    start = (now or datetime.now(UTC)).astimezone(UTC) - timedelta(days=1)
    return start, start + timedelta(days=14)


def _normalize_event(item: dict[str, Any], calendar_id: str) -> dict[str, Any]:
    event_id = item.get("id")
    updated = item.get("updated")
    if not isinstance(event_id, str) or not event_id or not isinstance(updated, str):
        raise ValueError("Google Calendar event is missing id or updated timestamp")
    occurred_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    summary = item.get("summary")
    content = summary if isinstance(summary, str) and summary else "Untitled calendar event"
    return {
        "source": "google_calendar",
        # Versioning makes source events immutable while allowing Calendar edits.
        "external_id": f"{event_id}:{updated}",
        "occurred_at": occurred_at,
        "content": content,
        "metadata": {
            "calendar_id": calendar_id,
            "calendar_event_id": event_id,
            "status": item.get("status"),
            "start": _event_time(item.get("start")),
            "end": _event_time(item.get("end")),
            "html_link": item.get("htmlLink"),
        },
        "sensitivity": "personal",
    }


def _event_time(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    timestamp = value.get("dateTime") or value.get("date")
    return timestamp if isinstance(timestamp, str) else None


def _rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _event_status(items: list[dict[str, Any]], event_id: str) -> str | None:
    for item in items:
        if item.get("id") == event_id:
            status = item.get("status")
            return status if isinstance(status, str) else None
    return None
