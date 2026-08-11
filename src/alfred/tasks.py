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
            raise ValueError("task title cannot be empty")
        task_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO tasks (id, title, state, due_at, source_event_id, created_at, updated_at)
            VALUES (?, ?, 'open', ?, ?, ?, ?)
            """,
            (task_id, normalized_title, due_at.isoformat() if due_at else None, source_event_id, now, now),
        )
        return Task(
            id=task_id,
            title=normalized_title,
            state="open",
            due_at=due_at,
            source_event_id=source_event_id,
        )
