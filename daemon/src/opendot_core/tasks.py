"""Canonical local tasks created from user-facing transports."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel


class Task(BaseModel):
    id: str
    title: str
    state: str
    due_at: datetime | None
    source_event_id: str


class TaskError(ValueError):
    """Raised when a task operation is invalid."""


class _UnsetType:
    def __repr__(self) -> str:
        return "UNSET"


UNSET = _UnsetType()


class TaskStore:
    @staticmethod
    def create(
        connection: sqlite3.Connection,
        *,
        title: str,
        source_event_id: str,
        due_at: datetime | None = None,
    ) -> Task:
        """Create one open task as part of the caller's transaction."""
        normalized_title = title.strip()
        if not normalized_title:
            raise TaskError("task title cannot be empty")
        task_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO tasks (id, title, state, due_at, source_event_id, created_at, updated_at)
            VALUES (?, ?, 'open', ?, ?, ?, ?)
            """,
            (task_id, normalized_title, due_at.isoformat() if due_at else None, source_event_id, now, now),
        )
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return TaskStore._from_row(row)

    @staticmethod
    def get(connection: sqlite3.Connection, task_id: str) -> Task | None:
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return TaskStore._from_row(row) if row is not None else None

    @staticmethod
    def upsert(
        connection: sqlite3.Connection,
        *,
        task_id: str | None = None,
        title: str,
        due_at: datetime | None | _UnsetType = UNSET,
        source_event_id: str | None = None,
    ) -> Task:
        """Create a new task when ``task_id`` is omitted, or update an existing one's title/due date.

        On update, omitting ``due_at`` leaves the existing due date
        unchanged; pass ``due_at=None`` explicitly to clear it. Without this
        distinction, a title-only update would silently wipe the due date.
        """
        if task_id is None:
            if source_event_id is None:
                raise TaskError("source_event_id is required to create a new task")
            create_due_at = None if isinstance(due_at, _UnsetType) else due_at
            return TaskStore.create(connection, title=title, source_event_id=source_event_id, due_at=create_due_at)
        normalized_title = title.strip()
        if not normalized_title:
            raise TaskError("task title cannot be empty")
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise TaskError(f"task does not exist: {task_id}")
        now = datetime.now(UTC).isoformat()
        new_due_at = row["due_at"] if isinstance(due_at, _UnsetType) else (due_at.isoformat() if due_at else None)
        connection.execute(
            "UPDATE tasks SET title = ?, due_at = ?, updated_at = ? WHERE id = ?",
            (normalized_title, new_due_at, now, task_id),
        )
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return TaskStore._from_row(row)

    @staticmethod
    def complete(connection: sqlite3.Connection, task_id: str) -> Task:
        """Mark an open task completed; completing an already-completed task is idempotent."""
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise TaskError(f"task does not exist: {task_id}")
        if row["state"] == "cancelled":
            raise TaskError("cannot complete a cancelled task")
        if row["state"] == "open":
            now = datetime.now(UTC).isoformat()
            connection.execute("UPDATE tasks SET state = 'completed', updated_at = ? WHERE id = ?", (now, task_id))
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return TaskStore._from_row(row)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            title=row["title"],
            state=row["state"],
            due_at=datetime.fromisoformat(row["due_at"]) if row["due_at"] else None,
            source_event_id=row["source_event_id"],
        )
