"""Work the owner asked for later, run at that time and reported back.

A reminder delivers text that already exists. "Check the order at 3 and text
me" is a different request: the text does not exist yet, because the work has
not happened. Alfred could previously only do the first, so a request of the
second kind either became a reminder to do it yourself, or -- what actually
happened -- Hermes quietly scheduled it in *its own* cron, a second job store
Alfred does not run, where it sat and never fired.

Section 2 makes Alfred the sole owner of schedules and delivery precisely to
prevent that split. This is the missing half of that ownership.

When the job comes due, `JobRunner` does not run the agent inline. It queues
the request exactly as an inbound Telegram message is queued, so the ordinary
bridge answers it on the next cycle with the whole normal pipeline: tool
selection, typing indicator, bubbles, feedback buttons. The reply is
indistinguishable from one the owner asked for directly, which is the point --
they asked for an answer at three o'clock, not a notification that a job ran.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .db import Database


class ScheduledTask(BaseModel):
    id: str
    run_at: datetime
    prompt: str
    chat_id: int
    daily: bool = False


class ScheduledTaskStore:
    """Create and inspect one-shot or daily agent tasks."""

    @staticmethod
    def schedule(
        connection: Any,
        *,
        prompt: str,
        run_at: datetime,
        chat_id: int,
        user_id: int | None = None,
        daily: bool = False,
        timezone_name: str | None = None,
        idempotency_key: str,
    ) -> ScheduledTask:
        """Persist a future agent turn addressed to one paired chat.

        ``prompt`` is stored verbatim and later becomes the request the agent
        answers, so it should read as an instruction the owner would send --
        "check the Cincinnati Open site for Sunday's grandstand order" -- not
        as a note to self.
        """
        instruction = " ".join(prompt.split())
        if not instruction:
            raise ValueError("scheduled task needs an instruction to run")
        if run_at.tzinfo is None:
            raise ValueError("scheduled task time must include a timezone")
        schedule: dict[str, Any] = {"run_at": run_at.isoformat()}
        if daily:
            # An IANA name, required rather than guessed: `str(tzinfo)` yields
            # "Eastern Daylight Time" on Windows, which ZoneInfo cannot load,
            # and a repeating task silently pinned to the wrong zone is worse
            # than one that refuses to be created. `schedule-brief` asks for
            # the same thing for the same reason.
            zone = timezone_name or getattr(run_at.tzinfo, "key", None)
            if not zone:
                raise ValueError(
                    "a daily task needs an IANA timezone name, e.g. America/New_York"
                )
            local = run_at.astimezone(ZoneInfo(zone))
            # Wall-clock time rather than a UTC offset, so a daily task keeps
            # its local hour across a daylight-saving change.
            schedule.update(
                {
                    "daily": True,
                    "time": f"{local.hour:02d}:{local.minute:02d}",
                    "timezone": zone,
                }
            )
        job_id = str(uuid4())
        payload = {
            "prompt": instruction,
            "chat_id": chat_id,
            "user_id": user_id,
            "destination": f"telegram:{chat_id}",
        }
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO jobs (id, kind, schedule_json, next_run_at, state, payload_json, idempotency_key, created_at, updated_at)
            VALUES (?, 'agent_task', ?, ?, 'active', ?, ?, ?, ?)
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
            "SELECT id, next_run_at, payload_json, schedule_json FROM jobs WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError("scheduled task insert was ignored without an existing job")
        stored = json.loads(row["payload_json"])
        return ScheduledTask(
            id=row["id"],
            run_at=datetime.fromisoformat(row["next_run_at"]),
            prompt=stored["prompt"],
            chat_id=int(stored["chat_id"]),
            daily=bool(json.loads(row["schedule_json"]).get("daily")),
        )

    @staticmethod
    def pending(database: Database) -> list[ScheduledTask]:
        """Every scheduled task still waiting to run, soonest first."""
        database.migrate()
        with database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, next_run_at, payload_json, schedule_json FROM jobs
                WHERE kind = 'agent_task' AND state = 'active' AND next_run_at IS NOT NULL
                ORDER BY next_run_at ASC
                """
            ).fetchall()
        tasks: list[ScheduledTask] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            tasks.append(
                ScheduledTask(
                    id=row["id"],
                    run_at=datetime.fromisoformat(row["next_run_at"]),
                    prompt=payload["prompt"],
                    chat_id=int(payload["chat_id"]),
                    daily=bool(json.loads(row["schedule_json"]).get("daily")),
                )
            )
        return tasks

    @staticmethod
    def cancel(database: Database, task_id: str) -> bool:
        """Stop a scheduled task; returns False when it was already gone.

        Marked ``completed`` rather than ``cancelled`` because the jobs table's
        CHECK constraint predates this and allows only active/paused/completed/
        failed. Clearing ``next_run_at`` is what actually stops it; the audit
        record is where the difference between "ran" and "called off" is kept,
        rather than inventing a schema change for one word.
        """
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                changed = connection.execute(
                    "UPDATE jobs SET state = 'completed', next_run_at = NULL, updated_at = ? "
                    "WHERE id = ? AND kind = 'agent_task' AND state = 'active'",
                    (datetime.now(UTC).isoformat(), task_id),
                ).rowcount
                if changed == 1:
                    AuditLog.append_in_transaction(
                        connection,
                        AuditEvent(
                            actor="user:cli",
                            client="scheduled_tasks",
                            tool="scheduled_task_cancel",
                            outcome="ok",
                            result={"job_id": task_id},
                        ),
                    )
        return changed == 1
