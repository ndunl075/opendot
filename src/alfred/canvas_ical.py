"""Read-only Canvas iCalendar sync without exposing the secret feed URL."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .connector_records import ConnectorRecordStore
from .db import Database
from .events import EventStore

MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_EVENTS = 2_000
_CANVAS_LINK = re.compile(r"/courses/(?P<course>\d+)/assignments/(?P<assignment>\d+)(?:/|$)")


class CanvasICalError(RuntimeError):
    """A deliberately URL-free error safe for logs and terminal output."""


@dataclass(frozen=True)
class CanvasICalResponse:
    body: str
    etag: str | None
    last_modified: str | None


class CanvasICalTransport(Protocol):
    def fetch(self, *, etag: str | None, last_modified: str | None) -> CanvasICalResponse | None: ...


class CanvasICalCredentialStore(Protocol):
    def store(self, name: str, value: str) -> None: ...


class CanvasICalClient:
    """Fetch one private ICS URL while keeping it out of every exception."""

    def __init__(
        self, feed_url: str, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        parsed = urlsplit(feed_url.strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Canvas calendar feed URL must be an absolute HTTPS URL")
        self._feed_url = feed_url.strip()
        self._client = httpx.Client(timeout=httpx.Timeout(30.0), transport=transport)

    def close(self) -> None:
        self._client.close()

    def fetch(self, *, etag: str | None, last_modified: str | None) -> CanvasICalResponse | None:
        headers: dict[str, str] = {"Accept": "text/calendar"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        try:
            response = self._client.get(self._feed_url, headers=headers)
            if response.status_code == 304:
                return None
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_FEED_BYTES:
                raise CanvasICalError("Canvas calendar feed exceeds the safe size limit")
            body = response.content
            if len(body) > MAX_FEED_BYTES:
                raise CanvasICalError("Canvas calendar feed exceeds the safe size limit")
            return CanvasICalResponse(
                body=body.decode("utf-8-sig"),
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
            )
        except CanvasICalError:
            raise
        except (httpx.HTTPError, UnicodeDecodeError, ValueError):
            raise CanvasICalError("Canvas calendar feed request failed") from None


class CanvasICalSyncResult(BaseModel):
    received: int = 0
    stored: int = 0
    active: int = 0
    unchanged: bool = False


class CanvasICalSetupResult(BaseModel):
    configured: bool = True
    repaired_duplicate: bool = False
    received: int = 0
    active: int = 0


def normalize_canvas_ical_feed_url(raw_feed_url: str) -> tuple[str, bool]:
    """Trim a pasted feed URL and safely repair one exact double-paste.

    The repair is intentionally narrow: Alfred only removes the second half
    when both HTTPS URLs are byte-for-byte identical. Anything ambiguous is
    left alone for normal URL validation to reject.
    """

    candidate = raw_feed_url.strip()
    duplicate_at = candidate.find("https://", len("https://"))
    if duplicate_at > 0:
        first = candidate[:duplicate_at].strip()
        second = candidate[duplicate_at:].strip()
        if first == second:
            return first, True
    return candidate, False


def setup_canvas_ical_feed(
    database: Database,
    credential_store: CanvasICalCredentialStore,
    raw_feed_url: str,
    *,
    secret_name: str = "canvas-ical-feed-url",
    transport: httpx.BaseTransport | None = None,
) -> CanvasICalSetupResult:
    """Validate, store, and ingest a private Canvas feed in one safe flow."""

    feed_url, repaired_duplicate = normalize_canvas_ical_feed_url(raw_feed_url)
    client = CanvasICalClient(feed_url, transport=transport)
    try:
        response = client.fetch(etag=None, last_modified=None)
    finally:
        client.close()
    if response is None:
        raise CanvasICalError("Canvas calendar feed returned no setup response")

    # Validate before changing the saved credential. The sync parses again so
    # its normal persistence and health bookkeeping stay on one code path.
    parsed = parse_canvas_ical(response.body)
    credential_store.store(secret_name, feed_url)
    result = CanvasICalSync(database, _SetupTransport(response)).sync()
    return CanvasICalSetupResult(
        repaired_duplicate=repaired_duplicate,
        received=len(parsed),
        active=result.active,
    )


class _SetupTransport:
    """Return the response already validated by the interactive setup flow."""

    def __init__(self, response: CanvasICalResponse) -> None:
        self.response = response

    def fetch(self, *, etag: str | None, last_modified: str | None) -> CanvasICalResponse:
        return self.response


class CanvasICalSync:
    """Persist a bounded feed snapshot and immutable minimized evidence."""

    connector_name = "canvas_ical"
    account_name = "self"

    def __init__(self, database: Database, transport: CanvasICalTransport) -> None:
        self.database = database
        self.transport = transport

    def sync(self) -> CanvasICalSyncResult:
        self.database.migrate()
        etag, last_modified = self._load_cursor()
        try:
            response = self.transport.fetch(etag=etag, last_modified=last_modified)
            if response is None:
                with self.database.connect() as connection:
                    with self.database.transaction(connection):
                        self._store_success(connection, etag, last_modified)
                        active = connection.execute(
                            """
                            SELECT COUNT(*) AS count FROM connector_records
                            WHERE connector = ? AND account = ? AND record_type = 'assignment' AND active = 1
                            """,
                            (self.connector_name, self.account_name),
                        ).fetchone()["count"]
                return CanvasICalSyncResult(active=int(active), unchanged=True)
            items = parse_canvas_ical(response.body)
        except Exception as error:
            self._store_error(error.__class__.__name__)
            if isinstance(error, CanvasICalError):
                raise
            raise CanvasICalError("Canvas calendar feed could not be parsed") from None

        stored = 0
        active_records: dict[str, dict[str, Any]] = {}
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                for item in items:
                    event = _event_for_store(item)
                    if EventStore.append(connection, **event).is_new:
                        stored += 1
                    if item["status"] != "cancelled":
                        active_records[item["record_id"]] = {
                            "title": item["title"],
                            "due_at": item["due_at"],
                            "course_name": item["course_name"],
                            "html_url": item["html_url"],
                            "kind": "assignment",
                            "status": item["status"],
                        }
                ConnectorRecordStore.replace_snapshot(
                    connection,
                    connector=self.connector_name,
                    account=self.account_name,
                    record_type="assignment",
                    records=active_records,
                )
                self._store_success(connection, response.etag, response.last_modified)
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="system:canvas_ical",
                        client="canvas_ical",
                        tool="canvas_ical_read_sync",
                        outcome="ok",
                        result={"received": len(items), "stored": stored, "active": len(active_records)},
                    ),
                )
        return CanvasICalSyncResult(received=len(items), stored=stored, active=len(active_records))

    def _load_cursor(self) -> tuple[str | None, str | None]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT cursor FROM sync_state WHERE connector = ? AND account = ?",
                (self.connector_name, self.account_name),
            ).fetchone()
        if not row or not row["cursor"]:
            return None, None
        try:
            cursor = json.loads(row["cursor"])
        except json.JSONDecodeError:
            return None, None
        return _optional_string(cursor.get("etag")), _optional_string(cursor.get("last_modified"))

    def _store_success(
        self, connection: Any, etag: str | None, last_modified: str | None
    ) -> None:
        now = datetime.now(UTC).isoformat()
        cursor = json.dumps(
            {"etag": etag, "last_modified": last_modified},
            sort_keys=True,
            separators=(",", ":"),
        )
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
            (self.connector_name, self.account_name, cursor, now, now),
        )

    def _store_error(self, reason: str) -> None:
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
                    (self.connector_name, self.account_name, reason, now),
                )


def parse_canvas_ical(text: str) -> list[dict[str, Any]]:
    """Parse the minimized VEVENT fields Alfred needs; ignore event bodies."""
    lines = _unfold_lines(text)
    upper_lines = {line.upper() for line in lines}
    if "BEGIN:VCALENDAR" not in upper_lines or "END:VCALENDAR" not in upper_lines:
        raise CanvasICalError("Canvas calendar feed is not a valid iCalendar document")
    events: list[dict[str, Any]] = []
    current: dict[str, list[tuple[dict[str, str], str]]] | None = None
    for line in lines:
        if line.upper() == "BEGIN:VEVENT":
            current = {}
            continue
        if line.upper() == "END:VEVENT":
            if current is not None:
                events.append(_normalize_component(current))
                if len(events) > MAX_EVENTS:
                    raise CanvasICalError("Canvas calendar feed exceeds the safe event limit")
            current = None
            continue
        if current is None:
            continue
        name, params, value = _parse_content_line(line)
        current.setdefault(name, []).append((params, value))
    if current is not None:
        raise CanvasICalError("Canvas calendar feed contains an incomplete event")
    return events


def _unfold_lines(text: str) -> list[str]:
    unfolded: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += raw[1:]
        else:
            unfolded.append(raw)
    return unfolded


def _parse_content_line(line: str) -> tuple[str, dict[str, str], str]:
    quoted = False
    split_at = -1
    for index, character in enumerate(line):
        if character == '"':
            quoted = not quoted
        elif character == ":" and not quoted:
            split_at = index
            break
    if split_at < 1:
        raise CanvasICalError("Canvas calendar feed contains an invalid content line")
    head, value = line[:split_at], line[split_at + 1 :]
    pieces = head.split(";")
    name = pieces[0].upper()
    params: dict[str, str] = {}
    for piece in pieces[1:]:
        key, separator, param_value = piece.partition("=")
        if separator:
            params[key.upper()] = param_value.strip('"')
    return name, params, value


def _normalize_component(
    properties: dict[str, list[tuple[dict[str, str], str]]]
) -> dict[str, Any]:
    uid = _required_property(properties, "UID")
    summary = _unescape(_property(properties, "SUMMARY") or "Untitled Canvas assignment").strip()
    due_params, due_value = _dated_property(properties)
    due_at = _parse_ical_datetime(due_value, due_params)
    recurrence_id = _property(properties, "RECURRENCE-ID") or ""
    status = (_property(properties, "STATUS") or "confirmed").casefold()
    sequence = _nonnegative_integer(_property(properties, "SEQUENCE"))
    html_url = _safe_public_url(_unescape(_property(properties, "URL") or ""))
    course_id, assignment_id = _canvas_ids(html_url)
    course_name = _course_name(properties, summary)
    stable_material = f"{uid}\n{recurrence_id}"
    record_id = hashlib.sha256(stable_material.encode("utf-8")).hexdigest()
    version_material = "\n".join(
        [stable_material, summary, due_at, status, str(sequence), html_url or "", course_name]
    )
    version = hashlib.sha256(version_material.encode("utf-8")).hexdigest()
    updated = _component_updated_at(properties, due_at)
    return {
        "record_id": record_id,
        "version": version,
        "title": summary,
        "due_at": due_at,
        "status": status,
        "html_url": html_url,
        "course_id": course_id,
        "course_name": course_name,
        "assignment_id": assignment_id or f"ical:{record_id}",
        "sequence": sequence,
        "occurred_at": updated,
    }


def _event_for_store(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "canvas",
        "external_id": f"canvas-ical:{item['record_id']}:{item['version']}",
        "occurred_at": item["occurred_at"],
        "content": item["title"],
        "metadata": {
            "assignment_id": item["assignment_id"],
            "course_id": item["course_id"],
            "course_name": item["course_name"],
            "due_at": item["due_at"],
            "html_url": item["html_url"],
            "kind": "assignment",
            "status": item["status"],
            "source_revision": item["sequence"],
            "source_connector": "canvas_ical",
        },
        "sensitivity": "personal",
    }


def _dated_property(
    properties: dict[str, list[tuple[dict[str, str], str]]]
) -> tuple[dict[str, str], str]:
    for name in ("DUE", "DTSTART"):
        values = properties.get(name)
        if values:
            return values[0]
    raise CanvasICalError("Canvas calendar event is missing a due date")


def _parse_ical_datetime(value: str, params: dict[str, str]) -> str:
    try:
        if params.get("VALUE", "").upper() == "DATE" or (len(value) == 8 and "T" not in value):
            day = datetime.strptime(value, "%Y%m%d").date()
            return datetime.combine(day, time(23, 59), tzinfo=UTC).isoformat()
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat()
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
        timezone_name = params.get("TZID")
        if timezone_name:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        return parsed.isoformat()
    except (ValueError, ZoneInfoNotFoundError):
        raise CanvasICalError("Canvas calendar event has an invalid due date") from None


def _component_updated_at(
    properties: dict[str, list[tuple[dict[str, str], str]]], due_at: str
) -> datetime:
    for name in ("LAST-MODIFIED", "DTSTAMP", "CREATED"):
        value = _property(properties, name)
        if value:
            parsed = _parse_ical_datetime(value, properties[name][0][0])
            result = datetime.fromisoformat(parsed)
            return result if result.tzinfo else result.replace(tzinfo=UTC)
    parsed_due = datetime.fromisoformat(due_at)
    return parsed_due if parsed_due.tzinfo else parsed_due.replace(tzinfo=UTC)


def _safe_public_url(value: str) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if "/feeds/calendars/" in parsed.path.casefold():
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _canvas_ids(url: str | None) -> tuple[str | None, str | None]:
    match = _CANVAS_LINK.search(urlsplit(url).path) if url else None
    if not match:
        return None, None
    return match.group("course"), match.group("assignment")


def _course_name(
    properties: dict[str, list[tuple[dict[str, str], str]]], summary: str
) -> str:
    category = _property(properties, "CATEGORIES")
    if category:
        first = _unescape(category).split(",", 1)[0].strip()
        if first:
            return first
    bracket = re.search(r"\s\[([^\[\]]+)\]\s*$", summary)
    return bracket.group(1).strip() if bracket else "Canvas"


def _required_property(
    properties: dict[str, list[tuple[dict[str, str], str]]], name: str
) -> str:
    value = _property(properties, name)
    if not value:
        raise CanvasICalError(f"Canvas calendar event is missing {name.lower()}")
    return value


def _property(
    properties: dict[str, list[tuple[dict[str, str], str]]], name: str
) -> str | None:
    values = properties.get(name)
    return values[0][1] if values else None


def _unescape(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _nonnegative_integer(value: str | None) -> int:
    try:
        parsed = int(value or "0")
    except ValueError:
        return 0
    return max(parsed, 0)
