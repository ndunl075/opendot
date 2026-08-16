"""Create persistent reminder jobs; execution belongs to the job runner.

One-shot reminders deliver text once and complete. Daily reminders keep the
same wall-clock local time across days (and daylight-saving changes) so
wake-up, bedtime, and study lock-in requests are ordinary schedules rather
than a second job system.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel


class ReminderJob(BaseModel):
    id: str
    run_at: datetime
    task_id: str
    daily: bool = False


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
        daily: bool = False,
        timezone_name: str | None = None,
    ) -> ReminderJob:
        """Persist a future reminder for an explicit delivery destination.

        ``chat_id`` remains only as a compatibility bridge for existing Telegram
        callers.  New integrations must supply a complete destination such as
        ``telegram:20`` or ``slack:D123``; the scheduler never guesses a channel.

        ``daily`` repeats the reminder at the same local wall-clock time.
        When set, ``timezone_name`` must be an IANA zone (or ``run_at`` must
        carry one) so a wake-up at 7:00 stays at 7:00 after a DST change.
        """
        if run_at.tzinfo is None:
            raise ValueError("reminder time must include a timezone")
        if destination is None:
            if chat_id is None:
                raise ValueError("reminder destination is required")
            destination = f"telegram:{chat_id}"
        if not destination.strip() or ":" not in destination:
            raise ValueError("reminder destination must be a non-empty channel:recipient value")
        schedule: dict[str, Any] = {"run_at": run_at.isoformat()}
        if daily:
            # Same refusal as daily agent tasks and morning briefs: Windows
            # ``str(tzinfo)`` is not loadable by ZoneInfo, and a repeating
            # reminder pinned to the wrong zone is worse than refusing create.
            zone = timezone_name or getattr(run_at.tzinfo, "key", None)
            if not zone:
                raise ValueError(
                    "a daily reminder needs an IANA timezone name, e.g. America/New_York"
                )
            local = run_at.astimezone(ZoneInfo(zone))
            schedule.update(
                {
                    "daily": True,
                    "time": f"{local.hour:02d}:{local.minute:02d}",
                    "timezone": zone,
                }
            )
        job_id = str(uuid4())
        payload: dict[str, Any] = {"destination": destination, "task_id": task_id, "text": text}
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO jobs (id, kind, schedule_json, next_run_at, state, payload_json, idempotency_key, created_at, updated_at)
            VALUES (?, 'reminder', ?, ?, 'active', ?, ?, ?, ?)
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
            raise RuntimeError("reminder job insert was ignored without an existing job")
        return ReminderJob(
            id=row["id"],
            run_at=datetime.fromisoformat(row["next_run_at"]),
            task_id=json.loads(row["payload_json"])["task_id"],
            daily=bool(json.loads(row["schedule_json"]).get("daily")),
        )
