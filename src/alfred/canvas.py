"""Read-only Canvas assignment sync for a private local Alfred install."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .connector_records import ConnectorRecordStore
from .db import Database
from .events import EventStore


class CanvasTransport(Protocol):
    def list_assignments(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]: ...


class CanvasClient:
    """Read-only client for current and historical Canvas assignments."""

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

    def list_historical_assignments(self, *, course_limit: int = 100) -> list[dict[str, Any]]:
        """Return assignments from current and completed enrolled courses.

        Canvas's upcoming/missing endpoints intentionally omit completed work.
        Course assignment reads fill that gap while retaining only the course
        label and compact submission state Alfred needs for long-term memory.
        """
        courses_by_id: dict[str, dict[str, Any]] = {}
        for enrollment_state in ("active", "completed"):
            for course in self._get_paginated(
                "/api/v1/courses",
                params={
                    "per_page": 100,
                    "enrollment_state": enrollment_state,
                    "enrollment_type": "student",
                },
            ):
                course_id = course.get("id")
                if isinstance(course_id, int | str):
                    courses_by_id[str(course_id)] = course
        historical: list[dict[str, Any]] = []
        for course in list(courses_by_id.values())[:course_limit]:
            course_id = course.get("id")
            if not isinstance(course_id, int | str):
                continue
            course_name = course.get("name") or course.get("course_code") or f"Course {course_id}"
            try:
                assignments = self._get_paginated(
                    f"/api/v1/courses/{course_id}/assignments",
                    params={"per_page": 100, "include[]": "submission", "order_by": "due_at"},
                )
            except httpx.HTTPStatusError as error:
                # Some schools seal concluded course content. Keep readable
                # history instead of failing the entire pass on one course.
                if error.response.status_code not in {401, 403, 404}:
                    raise
                continue
            for assignment in assignments:
                historical.append({**assignment, "course_name": str(course_name), "course_id": str(course_id)})
        return historical

    def _get_paginated(self, path: str, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = path
        request_params: dict[str, Any] | None = params or {"per_page": 100}
        for _ in range(100):
            if next_url is None:
                return items
            response = self._client.get(next_url, params=request_params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Canvas response must be a list")
            items.extend(item for item in payload if isinstance(item, dict))
            next_url = response.links.get("next", {}).get("url")
            request_params = None
        raise ValueError("Canvas pagination exceeded 100 pages")


class CanvasSyncResult(BaseModel):
    received: int
    stored: int
    upcoming: int
    missing: int
    historical: int = 0


class CanvasSync:
    """Store assignment status, deadline, course label, and source link only."""

    connector_name = "canvas"
    account_name = "self"

    def __init__(
        self,
        database: Database,
        transport: CanvasTransport,
        *,
        include_history: bool = False,
        history_course_limit: int = 100,
    ) -> None:
        self.database = database
        self.transport = transport
        self.include_history = include_history
        self.history_course_limit = history_course_limit

    def sync(self) -> CanvasSyncResult:
        self.database.migrate()
        try:
            upcoming, missing = self.transport.list_assignments()
            history_reader = getattr(self.transport, "list_historical_assignments", None)
            historical = (
                history_reader(course_limit=self.history_course_limit)
                if self.include_history and callable(history_reader)
                else []
            )
        except Exception as error:
            self._store_error(error.__class__.__name__)
            raise
        stored = 0
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                for kind, records in (("upcoming", upcoming), ("missing", missing), ("historical", historical)):
                    current_records: dict[str, dict[str, Any]] = {}
                    for record in records:
                        event = _normalize_assignment(record, kind)
                        if EventStore.append(connection, **event).is_new:
                            stored += 1
                        record_id = ":".join(
                            part
                            for part in (
                                str(event["metadata"].get("course_id") or ""),
                                str(event["metadata"]["assignment_id"]),
                            )
                            if part
                        )
                        current_records[record_id] = {
                            "title": event["content"],
                            "due_at": event["metadata"]["due_at"],
                            "course_name": event["metadata"]["course_name"],
                            "html_url": event["metadata"]["html_url"],
                            "submission_status": event["metadata"]["submission_status"],
                        }
                    ConnectorRecordStore.replace_snapshot(
                        connection,
                        connector=self.connector_name,
                        account=self.account_name,
                        record_type=kind,
                        records=current_records,
                    )
                self._store_success_in_transaction(connection)
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="system:canvas",
                        client="canvas",
                        tool="canvas_read_sync",
                        outcome="ok",
                        result={
                            "upcoming": len(upcoming),
                            "missing": len(missing),
                            "historical": len(historical),
                            "stored": stored,
                        },
                    ),
                )
        return CanvasSyncResult(
            received=len(upcoming) + len(missing) + len(historical),
            stored=stored,
            upcoming=len(upcoming),
            missing=len(missing),
            historical=len(historical),
        )

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
    submission = item.get("submission") if isinstance(item.get("submission"), dict) else {}
    submission_status = submission.get("workflow_state") or item.get("workflow_state")
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
            "submission_status": str(submission_status) if submission_status is not None else None,
            "course_id": str(item["course_id"]) if isinstance(item.get("course_id"), int | str) else None,
        },
        "sensitivity": "personal",
    }
