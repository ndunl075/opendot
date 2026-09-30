"""Repeating reminders that stop when a linked task is completed."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from .destinations import resolve_destination


class NagJob(BaseModel):
    id: str
    run_at: datetime
    task_id: str
    attempt: int
    max_attempts: int


class NagStore:
    @staticmethod
    def create(
        connection: sqlite3.Connection,
        *,
        run_at: datetime,
        task_id: str,
        text: str,
        destination: str | None = None,
        chat_id: int | None = None,
        interval_hours: float,
        max_attempts: int,
        attempt: int = 1,
        idempotency_key: str,
    ) -> NagJob:
        """Persist a nag job that re-checks task state on every fire.

        ``chat_id`` remains only as a compatibility bridge for existing Telegram
        callers. New integrations must supply a complete destination such as
        ``telegram:20`` or ``slack:D123``.
        """
        if run_at.tzinfo is None:
            raise ValueError("nag first run time must include a timezone")
        if interval_hours <= 0:
            raise ValueError("nag interval_hours must be positive")
        if max_attempts < 1:
            raise ValueError("nag max_attempts must be at least 1")
        if attempt < 1:
            raise ValueError("nag attempt must be at least 1")
        destination = resolve_destination(destination, chat_id, noun="nag")
        schedule: dict[str, Any] = {"interval_hours": interval_hours}
        job_id = str(uuid4())
        payload: dict[str, Any] = {
            "destination": destination,
            "task_id": task_id,
            "text": text,
            "attempt": attempt,
            "max_attempts": max_attempts,
        }
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO jobs (id, kind, schedule_json, next_run_at, state, payload_json, idempotency_key, created_at, updated_at)
            VALUES (?, 'nag', ?, ?, 'active', ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO NOTHING
            """,
            (
                job_id,
                json.dumps(schedule, sort_keys=True, separators=(",", ":")),
                run_at.astimezone(UTC).isoformat(),
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                idempotency_key,
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT id, next_run_at, payload_json FROM jobs WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError("nag job insert was ignored without an existing job")
        loaded = json.loads(row["payload_json"])
        return NagJob(
            id=row["id"],
            run_at=datetime.fromisoformat(row["next_run_at"]),
            task_id=loaded["task_id"],
            attempt=loaded["attempt"],
            max_attempts=loaded["max_attempts"],
        )

    @staticmethod
    def next_run_at(*, after: datetime, interval_hours: float) -> datetime:
        return after.astimezone(UTC) + timedelta(hours=interval_hours)
