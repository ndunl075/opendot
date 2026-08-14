"""Persistent job execution with transactional outbox hand-off."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .brief_schedule import next_daily_occurrence
from .briefing import BriefingService
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
                    SELECT id, kind, schedule_json, next_run_at, payload_json
                    FROM jobs
                    WHERE state = 'active' AND next_run_at IS NOT NULL AND next_run_at <= ?
                    ORDER BY next_run_at ASC, id ASC
                    """,
                    (run_at.isoformat(),),
                ).fetchall()
                for job in due_jobs:
                    scheduled_at = datetime.fromisoformat(job["next_run_at"])
                    payload = json.loads(job["payload_json"])
                    late = run_at - scheduled_at > self.late_after
                    if job["kind"] in {"reminder", "telegram_reminder"}:
                        prefix = f"Late reminder (scheduled {scheduled_at.isoformat()}): " if late else "Reminder: "
                        text = f"{prefix}{payload['text']}"
                        destination = payload.get("destination") or f"telegram:{payload['chat_id']}"
                        next_run_at = None
                        state = "completed"
                    elif job["kind"] in {"morning_brief", "telegram_morning_brief"}:
                        schedule = json.loads(job["schedule_json"])
                        brief_service = BriefingService(self.database)
                        text = brief_service.morning_brief(
                            run_at,
                            scheduled_at=scheduled_at if late else None,
                            timezone_name=schedule["timezone"],
                        ).render()
                        destination = payload.get("destination") or f"telegram:{payload['chat_id']}"
                        next_run_at = next_daily_occurrence(schedule, run_at).isoformat()
                        state = "active"
                    else:
                        continue
                    outbox = Outbox.enqueue(
                        connection,
                        destination=destination,
                        payload={"text": text, "task_id": payload.get("task_id")},
                        idempotency_key=f"job-delivery:{job['id']}:{job['next_run_at']}",
                        job_id=job["id"],
                    )
                    connection.execute(
                        """
                        UPDATE jobs
                        SET state = ?, next_run_at = ?, updated_at = ?
                        WHERE id = ? AND state = 'active'
                        """,
                        (state, next_run_at, run_at.isoformat(), job["id"]),
                    )
                    AuditLog.append_in_transaction(
                        connection,
                        AuditEvent(
                            actor="system:scheduler",
                            client="jobs",
                            tool="morning_brief_deliver" if job["kind"] in {"morning_brief", "telegram_morning_brief"} else "reminder_deliver",
                            outcome="outbox_enqueued",
                            arguments={"job_id": job["id"], "scheduled_at": scheduled_at.isoformat()},
                            result={"outbox_id": outbox.id, "late": late},
                            correlation_id=job["id"],
                        ),
                    )
                    executed.append(ExecutedJob(id=job["id"], outbox_id=outbox.id, late=late))
        return executed
