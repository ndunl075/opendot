"""Persistent job execution with transactional outbox hand-off."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .db import Database
from .outbox import Outbox


class ExecutedJob(BaseModel):
    id: str
    outbox_id: str
    late: bool


class JobRunner:
    """Claims due one-shot jobs, emits delivery intents, and marks them complete."""

    late_after = timedelta(minutes=1)

    def __init__(self, database: Database) -> None:
        self.database = database

    def run_due(self, now: datetime | None = None) -> list[ExecutedJob]:
        """Run each active due job once; retries are owned by the delivery outbox."""
        run_at = (now or datetime.now(UTC)).astimezone(UTC)
        self.database.migrate()
        executed: list[ExecutedJob] = []
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                due_jobs = connection.execute(
                    """
                    SELECT id, kind, next_run_at, payload_json
                    FROM jobs
                    WHERE state = 'active' AND next_run_at IS NOT NULL AND next_run_at <= ?
                    ORDER BY next_run_at ASC, id ASC
                    """,
                    (run_at.isoformat(),),
                ).fetchall()
                for job in due_jobs:
                    if job["kind"] != "telegram_reminder":
                        continue
                    scheduled_at = datetime.fromisoformat(job["next_run_at"])
                    payload = json.loads(job["payload_json"])
                    late = run_at - scheduled_at > self.late_after
                    prefix = f"Late reminder (scheduled {scheduled_at.isoformat()}): " if late else "Reminder: "
                    outbox = Outbox.enqueue(
                        connection,
                        destination=f"telegram:{payload['chat_id']}",
                        payload={"text": f"{prefix}{payload['text']}", "task_id": payload["task_id"]},
                        idempotency_key=f"job-delivery:{job['id']}:{job['next_run_at']}",
                        job_id=job["id"],
                    )
                    connection.execute(
                        """
                        UPDATE jobs
                        SET state = 'completed', next_run_at = NULL, updated_at = ?
                        WHERE id = ? AND state = 'active'
                        """,
                        (run_at.isoformat(), job["id"]),
                    )
                    AuditLog.append_in_transaction(
                        connection,
                        AuditEvent(
                            actor="system:scheduler",
                            client="jobs",
                            tool="reminder_deliver",
                            outcome="outbox_enqueued",
                            arguments={"job_id": job["id"], "scheduled_at": scheduled_at.isoformat()},
                            result={"outbox_id": outbox.id, "late": late},
                            correlation_id=job["id"],
                        ),
                    )
                    executed.append(ExecutedJob(id=job["id"], outbox_id=outbox.id, late=late))
        return executed
