"""Create persistent reminder jobs; execution belongs to the job runner."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel


class ReminderJob(BaseModel):
    id: str
    run_at: datetime
    task_id: str


class ReminderStore:
    @staticmethod
    def create(
        connection: sqlite3.Connection,
        *,
        run_at: datetime,
        task_id: str,
        destination: str | None = None,
        chat_id: int | None = None,
        text: str,
        idempotency_key: str,
    ) -> ReminderJob:
        """Persist a future one-shot reminder for an explicit delivery destination.

        ``chat_id`` remains only as a compatibility bridge for existing Telegram
        callers.  New integrations must supply a complete destination such as
        ``telegram:20`` or ``slack:D123``; the scheduler never guesses a channel.
        """
        if run_at.tzinfo is None:
            raise ValueError("reminder time must include a timezone")
        if destination is None:
            if chat_id is None:
                raise ValueError("reminder destination is required")
            destination = f"telegram:{chat_id}"
        if not destination.strip() or ":" not in destination:
            raise ValueError("reminder destination must be a non-empty channel:recipient value")
        job_id = str(uuid4())
        payload: dict[str, Any] = {"destination": destination, "task_id": task_id, "text": text}
        connection.execute(
            """
            INSERT INTO jobs (id, kind, schedule_json, next_run_at, state, payload_json, idempotency_key, created_at, updated_at)
            VALUES (?, 'reminder', ?, ?, 'active', ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO NOTHING
            """,
            (
                job_id,
                json.dumps({"run_at": run_at.isoformat()}, sort_keys=True, separators=(",", ":")),
                run_at.astimezone(UTC).isoformat(),
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                idempotency_key,
                datetime.now(UTC).isoformat(),
                datetime.now(UTC).isoformat(),
            ),
        )
        row = connection.execute(
            "SELECT id, next_run_at, payload_json FROM jobs WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError("reminder job insert was ignored without an existing job")
        return ReminderJob(
            id=row["id"],
            run_at=datetime.fromisoformat(row["next_run_at"]),
            task_id=json.loads(row["payload_json"])["task_id"],
        )
