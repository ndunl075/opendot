"""Persistent job execution with transactional outbox hand-off."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .brief_schedule import next_daily_occurrence
from .briefing import BriefingService
from .db import Database
from .events import EventStore
from .important_dates import ImportantDateStore
from .nags import NagStore
from .outbox import Outbox
from .tasks import TaskStore


class ExecutedJob(BaseModel):
    id: str
    #: None for a scheduled agent task: it queues work rather than a
    #: message, and the reply is enqueued later by the bridge that answers it.
    outbox_id: str | None = None
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
                        schedule = json.loads(job["schedule_json"])
                        # Wake-up / bedtime / study lock-in are just daily
                        # reminders: same delivery path as a one-shot, then
                        # the wall-clock schedule advances one day.
                        if schedule.get("annual"):
                            # Birthdays and important dates: deliver, then roll
                            # the reminder and its linked task to next year.
                            next_run_at = ImportantDateStore.advance_after_delivery(
                                connection,
                                job_id=job["id"],
                                schedule=schedule,
                                payload=payload,
                                after=run_at,
                            ).isoformat()
                            state = "active"
                        elif schedule.get("daily"):
                            next_run_at = next_daily_occurrence(schedule, run_at).isoformat()
                            state = "active"
                        else:
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
                    elif job["kind"] == "agent_task":
                        # A scheduled *task*, not a scheduled message: the user
                        # asked for something to be done later and reported
                        # back, so there is no text to deliver yet -- it does
                        # not exist until the work runs.
                        #
                        # The work is deliberately not run here. An agent turn
                        # takes tens of seconds and spawns a subprocess that
                        # reads this same database, and this method holds a
                        # write transaction. Instead this queues the request
                        # exactly the way an inbound Telegram message is
                        # queued, so `hermes_bridge` picks it up on the next
                        # cycle and answers it through the whole normal path:
                        # tool selection, typing indicator, bubbles, feedback
                        # buttons. The user cannot tell it came from a
                        # schedule rather than from them, which is the point.
                        chat_id = payload.get("chat_id")
                        if not isinstance(chat_id, int):
                            continue
                        EventStore.append(
                            connection,
                            source="telegram",
                            external_id=f"scheduled:{job['id']}:{job['next_run_at']}",
                            occurred_at=run_at,
                            content=str(payload["prompt"]),
                            metadata={
                                "chat_id": chat_id,
                                "user_id": payload.get("user_id"),
                                "agent_deferred": True,
                                # Named so a scheduled turn is identifiable in
                                # the event log without changing how it is
                                # answered.
                                "scheduled_job_id": job["id"],
                            },
                            sensitivity="personal",
                        )
                        schedule = json.loads(job["schedule_json"])
                        repeat_daily = bool(schedule.get("daily"))
                        connection.execute(
                            """
                            UPDATE jobs SET state = ?, next_run_at = ?, updated_at = ?
                            WHERE id = ? AND state = 'active'
                            """,
                            (
                                "active" if repeat_daily else "completed",
                                next_daily_occurrence(schedule, run_at).isoformat() if repeat_daily else None,
                                run_at.isoformat(),
                                job["id"],
                            ),
                        )
                        AuditLog.append_in_transaction(
                            connection,
                            AuditEvent(
                                actor="system:scheduler",
                                client="jobs",
                                tool="agent_task_queue",
                                outcome="queued",
                                arguments={"job_id": job["id"], "scheduled_at": scheduled_at.isoformat()},
                                result={"late": late},
                                correlation_id=job["id"],
                            ),
                        )
                        executed.append(ExecutedJob(id=job["id"], outbox_id=None, late=late))
                        continue
                    elif job["kind"] == "nag":
                        schedule = json.loads(job["schedule_json"])
                        interval_hours = float(schedule["interval_hours"])
                        attempt = int(payload["attempt"])
                        max_attempts = int(payload["max_attempts"])
                        task = TaskStore.get(connection, payload["task_id"])
                        if task is None or task.state != "open":
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
                                    tool="nag_silence",
                                    outcome="task_closed",
                                    arguments={"job_id": job["id"], "task_id": payload["task_id"]},
                                    result={"late": late},
                                    correlation_id=job["id"],
                                ),
                            )
                            executed.append(ExecutedJob(id=job["id"], outbox_id=None, late=late))
                            continue
                        destination = payload.get("destination") or f"telegram:{payload['chat_id']}"
                        if attempt >= max_attempts:
                            text = f"Last reminder ({attempt} of {max_attempts}): {payload['text']}"
                            next_run_at = None
                            state = "completed"
                            updated_payload = payload
                            audit_tool = "nag_deliver_final"
                        else:
                            prefix = (
                                f"Late reminder (scheduled {scheduled_at.isoformat()}): "
                                if late
                                else "Reminder: "
                            )
                            text = f"{prefix}{payload['text']}"
                            next_run_at = NagStore.next_run_at(after=run_at, interval_hours=interval_hours).isoformat()
                            state = "active"
                            updated_payload = {**payload, "attempt": attempt + 1}
                            audit_tool = "nag_deliver"
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
                        SET state = ?, next_run_at = ?, payload_json = ?, updated_at = ?
                        WHERE id = ? AND state = 'active'
                        """,
                        (
                            state,
                            next_run_at,
                            json.dumps(updated_payload, sort_keys=True, separators=(",", ":"))
                            if job["kind"] == "nag"
                            else job["payload_json"],
                            run_at.isoformat(),
                            job["id"],
                        ),
                    )
                    if job["kind"] == "nag":
                        deliver_tool = audit_tool
                    elif job["kind"] in {"morning_brief", "telegram_morning_brief"}:
                        deliver_tool = "morning_brief_deliver"
                    else:
                        deliver_tool = "reminder_deliver"
                    AuditLog.append_in_transaction(
                        connection,
                        AuditEvent(
                            actor="system:scheduler",
                            client="jobs",
                            tool=deliver_tool,
                            outcome="outbox_enqueued",
                            arguments={"job_id": job["id"], "scheduled_at": scheduled_at.isoformat()},
                            result={"outbox_id": outbox.id, "late": late},
                            correlation_id=job["id"],
                        ),
                    )
                    executed.append(ExecutedJob(id=job["id"], outbox_id=outbox.id, late=late))
        return executed
