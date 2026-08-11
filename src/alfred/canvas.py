"""Read-only Canvas assignment sync for a private local Alfred install."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .db import Database
from .events import EventStore


class CanvasTransport(Protocol):
    def list_assignments(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]: ...


class CanvasClient:
    """Client for Canvas's current-user upcoming and missing assignment endpoints."""

    def __init__(
        self, base_url: str, access_token: str, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("Canvas base URL must use HTTPS")
        if not access_token.strip():
            raise ValueError("Canvas access token must not be empty")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=httpx.Timeout(30.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def list_assignments(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return (
            self._get_paginated("/api/v1/users/self/upcoming_events"),
            self._get_paginated("/api/v1/users/self/missing_submissions"),
        )

    def _get_paginated(self, path: str) -> list[dict[str, Any]]:
        response = self._client.get(path, params={"per_page": 100})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Canvas response must be a list")
        return [item for item in payload if isinstance(item, dict)]


class CanvasSyncResult(BaseModel):
    received: int
    stored: int
    upcoming: int
    missing: int


class CanvasSync:
    """Store assignment status, deadline, course label, and source link only."""

    connector_name = "canvas"
    account_name = "self"

    def __init__(self, database: Database, transport: CanvasTransport) -> None:
        self.database = database
        self.transport = transport

    def sync(self) -> CanvasSyncResult:
        self.database.migrate()
        try:
            upcoming, missing = self.transport.list_assignments()
        except Exception as error:
            self._store_error(error.__class__.__name__)
            raise
        stored = 0
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                for kind, records in (("upcoming", upcoming), ("missing", missing)):
                    for record in records:
                        if EventStore.append(connection, **_normalize_assignment(record, kind)).is_new:
                            stored += 1
                self._store_success_in_transaction(connection)
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="system:canvas",
                        client="canvas",
                        tool="canvas_read_sync",
                        outcome="ok",
                        result={"upcoming": len(upcoming), "missing": len(missing), "stored": stored},
                    ),
                )
        return CanvasSyncResult(received=len(upcoming) + len(missing), stored=stored, upcoming=len(upcoming), missing=len(missing))

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


def _normalize_assignment(item: dict[str, Any], kind: str) -> dict[str, Any]:
    assignment_id = item.get("assignment_id", item.get("id"))
    if not isinstance(assignment_id, int | str):
        raise ValueError("Canvas assignment is missing an ID")
    updated = item.get("updated_at") or item.get("due_at") or item.get("created_at")
    if not isinstance(updated, str):
        raise ValueError("Canvas assignment is missing a timestamp")
    occurred_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    title = item.get("assignment", {}).get("name") if isinstance(item.get("assignment"), dict) else item.get("title") or item.get("name")
    if not isinstance(title, str) or not title:
        title = "Untitled Canvas assignment"
    course_name = item.get("context_name") or item.get("course_name")
    return {
        "source": "canvas",
        "external_id": f"{kind}:{assignment_id}:{updated}",
        "occurred_at": occurred_at,
        "content": title,
        "metadata": {
            "assignment_id": str(assignment_id),
            "kind": kind,
            "due_at": item.get("due_at"),
            "course_name": course_name if isinstance(course_name, str) else None,
            "html_url": item.get("html_url") if isinstance(item.get("html_url"), str) else None,
        },
        "sensitivity": "personal",
    }
