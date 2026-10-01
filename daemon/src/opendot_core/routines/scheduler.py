"""Runs the built-in routines from the always-on loop.

Each routine owns one row in the ``jobs`` table (kind ``routine``, idempotency key ``routine:<name>``).
The job runner skips kinds it does not know, so the row is the routine's registration: it carries the
schedule, the next run and the routine's small state, and it gives every delivery a ``job_id`` so the
delivery workers also hold it during quiet hours. A model pass is async work that the sync job runner
must not do inside its write transaction, so this scheduler runs beside it (``OpenDotRunner(routines=...)``).

Order of checks for a scheduled run: enabled, due (a late run is caught up once, never made up for a time
before the routine was set up), outside quiet hours. A run with nothing to say delivers nothing.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ..audit import AuditEvent, AuditLog
from ..db import Database
from ..outbox import Outbox
from ..quiet_hours import QuietHours
from ..settings_store import SettingsStore
from .base import Outcome, Routine, RoutineContext, RoutineState
from .inbox_triage import InboxTriageRoutine
from .model_pass import ModelPass
from .morning_brief import MorningBriefRoutine
from .weekly_review import WeeklyReviewRoutine

JOB_KIND = "routine"
ERROR_RETRY = timedelta(minutes=10)


def default_routines() -> list[Routine]:
    return [MorningBriefRoutine(), InboxTriageRoutine(), WeeklyReviewRoutine()]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class RoutineResult:
    routine: str
    status: str
    """``delivered``, ``nothing_new``, ``skipped_quiet_hours``, ``disabled`` or ``error``."""
    outbox_id: str | None = None
    text: str | None = None
    model_called: bool = False
    used_model: bool = False
    fallback_reason: str = ""
    credits: float = 0.0
    manual: bool = False
    detail: str = ""


@dataclass
class RoutineStatus:
    name: str
    enabled: bool
    schedule: str
    next_run_at: datetime | None
    last_run_at: datetime | None
    settings: dict[str, Any] = field(default_factory=dict)


class RoutineScheduler:
    def __init__(
        self,
        database: Database,
        *,
        loop: Any | None = None,
        loop_factory: Callable[[], Any] | None = None,
        pull_requests: Callable[[], Any] | None = None,
        clock: Callable[[], datetime] = _utc_now,
        quiet_hours: Callable[[], QuietHours] | None = None,
        routines: list[Routine] | None = None,
    ) -> None:
        self.database = database
        self.settings = SettingsStore(database)
        self.model = ModelPass(loop, factory=loop_factory)
        self.pull_requests = pull_requests
        self.clock = clock
        self._quiet_hours = quiet_hours or self._stored_quiet_hours
        self.routines = {routine.name: routine for routine in (routines or default_routines())}
        self._retry_after: dict[str, datetime] = {}
        self.database.migrate()

    # -- settings -----------------------------------------------------------------

    def settings_for(self, routine: Routine) -> Any:
        return self.settings.get(routine.settings_key, routine.settings_cls, routine.settings_cls())

    def configure(self, name: str, **changes: Any) -> Any:
        """Change one routine's settings (enable, disable, schedule). Validates through the settings model."""
        routine = self.routine(name)
        current = self.settings_for(routine)
        updated = routine.settings_cls.model_validate({**current.model_dump(), **changes})
        self.settings.set(routine.settings_key, updated)
        return updated

    def routine(self, name: str) -> Routine:
        try:
            return self.routines[name]
        except KeyError:
            raise KeyError(f"unknown routine {name!r}; choose one of: {', '.join(sorted(self.routines))}") from None

    def _stored_quiet_hours(self) -> QuietHours:
        from ..api.models import QuietHoursSettings
        from ..wall_clock import parse_hhmm

        stored = self.settings.get("quiet_hours", QuietHoursSettings, None)
        if stored is not None:
            if not stored.enabled:
                return QuietHours.disabled()
            try:
                return QuietHours(start=parse_hhmm(stored.start), end=parse_hhmm(stored.end), timezone=stored.timezone)
            except ValueError:
                return QuietHours.disabled()
        try:
            return QuietHours.from_environment()
        except ValueError:
            return QuietHours.disabled()

    # -- job rows -----------------------------------------------------------------

    def _schedule_json(self, routine: Routine, settings: Any) -> str:
        if routine.kind == "interval":
            schedule: dict[str, Any] = {"every_minutes": settings.check_every_minutes}
        else:
            schedule = {"time": settings.time, "timezone": settings.timezone}
            if routine.kind == "weekly":
                schedule["weekday"] = settings.weekday
        return json.dumps(schedule, sort_keys=True)

    def _sync_job(
        self, routine: Routine, settings: Any, now: datetime, *, create: bool = True
    ) -> tuple[str, RoutineState] | None:
        """Create or refresh the routine's job row; returns its id and state.

        A routine that is switched off and has never run gets no row (``create=False``), so an idle
        install adds nothing to the database."""
        key = f"routine:{routine.name}"
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                row = connection.execute(
                    "SELECT id, payload_json, state, next_run_at, schedule_json FROM jobs WHERE idempotency_key = ?",
                    (key,),
                ).fetchone()
                if row is None and not create:
                    return None
                if row is None:
                    job_id = str(uuid.uuid4())
                    state = RoutineState(since=now)
                    payload = {"routine": routine.name, "destination": settings.destination, "state": state.model_dump(mode="json")}
                    connection.execute(
                        "INSERT INTO jobs (id, kind, schedule_json, next_run_at, state, payload_json, idempotency_key, "
                        "created_at, updated_at) VALUES (?, ?, ?, NULL, 'paused', ?, ?, ?, ?)",
                        (job_id, JOB_KIND, self._schedule_json(routine, settings), json.dumps(payload, sort_keys=True), key,
                         now.isoformat(), now.isoformat()),
                    )
                    row = connection.execute(
                        "SELECT id, payload_json, state, next_run_at, schedule_json FROM jobs WHERE id = ?", (job_id,)
                    ).fetchone()
                payload = json.loads(row["payload_json"])
                state = RoutineState.model_validate(payload.get("state") or {})
                if state.since is None:
                    state.since = now
                wanted_state = "active" if settings.enabled else "paused"
                if settings.enabled and row["state"] != "active":
                    state.since = now  # switched on just now: never make up a time from before that
                next_run = routine.next_occurrence(settings, now, state) if settings.enabled else None
                changed = (
                    row["state"] != wanted_state
                    or row["schedule_json"] != self._schedule_json(routine, settings)
                    or payload.get("destination") != settings.destination
                    or row["next_run_at"] != (next_run.isoformat() if next_run else None)
                )
                if changed:
                    payload["destination"] = settings.destination
                    payload["state"] = state.model_dump(mode="json")
                    connection.execute(
                        "UPDATE jobs SET state = ?, schedule_json = ?, next_run_at = ?, payload_json = ?, updated_at = ? "
                        "WHERE id = ?",
                        (wanted_state, self._schedule_json(routine, settings), next_run.isoformat() if next_run else None,
                         json.dumps(payload, sort_keys=True), now.isoformat(), row["id"]),
                    )
        return str(row["id"]), state

    # -- running ------------------------------------------------------------------

    def run_due(self, now: datetime | None = None) -> list[RoutineResult]:
        """Run every routine that is enabled, due and outside quiet hours. Called every loop cycle."""
        moment = (now or self.clock()).astimezone(UTC)
        results: list[RoutineResult] = []
        for routine in self.routines.values():
            settings = self.settings_for(routine)
            synced = self._sync_job(routine, settings, moment, create=settings.enabled)
            if synced is None or not settings.enabled:
                continue
            job_id, state = synced
            if self._retry_after.get(routine.name, moment) > moment:
                continue
            due, scheduled_at = routine.due(settings, state, moment)
            if not due:
                continue
            if self._quiet_hours().is_active(moment):
                results.append(RoutineResult(routine.name, "skipped_quiet_hours"))
                continue
            results.append(self._execute(routine, settings, job_id, state, moment, scheduled_at, manual=False))
        return results

    def run_named(self, name: str, now: datetime | None = None) -> RoutineResult:
        """A manual run (``opendot routines run``): ignores the schedule, the enabled switch and quiet hours,
        but not the budgets or the kill switch, and delivers the same way."""
        routine = self.routine(name)
        moment = (now or self.clock()).astimezone(UTC)
        settings = self.settings_for(routine)
        synced = self._sync_job(routine, settings, moment)
        assert synced is not None
        job_id, state = synced
        return self._execute(routine, settings, job_id, state, moment, None, manual=True)

    def _execute(
        self,
        routine: Routine,
        settings: Any,
        job_id: str,
        state: RoutineState,
        now: datetime,
        scheduled_at: datetime | None,
        *,
        manual: bool,
    ) -> RoutineResult:
        context = RoutineContext(database=self.database, model=self.model, pull_requests=self.pull_requests)
        try:
            outcome = routine.run(context, settings, state, now, scheduled_at)
        except Exception as error:
            self._retry_after[routine.name] = now + ERROR_RETRY
            self._audit(routine, "error", {"error": type(error).__name__}, manual=manual)
            return RoutineResult(routine.name, "error", detail=f"{type(error).__name__}: {error}", manual=manual)
        outbox_id = self._finish(routine, settings, job_id, state, now, scheduled_at, outcome, manual=manual)
        return RoutineResult(
            routine.name,
            outcome.status if outbox_id or outcome.status != "delivered" else "nothing_new",
            outbox_id=outbox_id,
            text=outcome.text,
            model_called=outcome.model_called,
            used_model=outcome.used_model,
            fallback_reason=outcome.fallback_reason,
            credits=outcome.credits,
            manual=manual,
        )

    def _finish(
        self,
        routine: Routine,
        settings: Any,
        job_id: str,
        state: RoutineState,
        now: datetime,
        scheduled_at: datetime | None,
        outcome: Outcome,
        *,
        manual: bool,
    ) -> str | None:
        outbox_id: str | None = None
        if outcome.seen_ids is not None:
            state.seen_ids = outcome.seen_ids
        if routine.kind == "interval" or not manual:
            state.last_run_at = now
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                if outcome.status == "delivered" and outcome.text:
                    slot = f"manual:{now.isoformat()}" if manual else (scheduled_at or now).isoformat()
                    suffix = f":{outcome.dedupe}" if outcome.dedupe else ""
                    message = Outbox.enqueue(
                        connection,
                        destination=settings.destination,
                        payload={"text": outcome.text, "routine": routine.name},
                        idempotency_key=f"routine:{routine.name}:{slot}{suffix}",
                        job_id=job_id,
                    )
                    outbox_id = message.id
                row = connection.execute("SELECT payload_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
                payload = json.loads(row["payload_json"])
                payload["state"] = state.model_dump(mode="json")
                next_run = routine.next_occurrence(settings, now, state) if settings.enabled else None
                connection.execute(
                    "UPDATE jobs SET payload_json = ?, next_run_at = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(payload, sort_keys=True), next_run.isoformat() if next_run else None, now.isoformat(), job_id),
                )
        self._audit(
            routine,
            "delivered" if outbox_id else "nothing_new",
            {
                "model_called": outcome.model_called,
                "used_model": outcome.used_model,
                "fallback": bool(outcome.fallback_reason),
                "credits": round(outcome.credits, 4),
                **outcome.details,
            },
            manual=manual,
        )
        return outbox_id

    def _audit(self, routine: Routine, outcome: str, result: dict[str, Any], *, manual: bool) -> None:
        # Counts and flags only: never a subject, a sender, a snippet or any message text.
        AuditLog(self.database).append(
            AuditEvent(
                actor="system:routines",
                client="routines",
                tool=f"routine_{routine.name}",
                outcome=outcome,
                arguments={"manual": manual},
                result=result,
            )
        )

    # -- reading ------------------------------------------------------------------

    def status(self, now: datetime | None = None) -> list[RoutineStatus]:
        moment = (now or self.clock()).astimezone(UTC)
        report: list[RoutineStatus] = []
        for routine in self.routines.values():
            settings = self.settings_for(routine)
            synced = self._sync_job(routine, settings, moment, create=False)
            state = synced[1] if synced is not None else RoutineState(since=moment)
            report.append(
                RoutineStatus(
                    name=routine.name,
                    enabled=settings.enabled,
                    schedule=self._describe(routine, settings),
                    next_run_at=routine.next_occurrence(settings, moment, state) if settings.enabled else None,
                    last_run_at=state.last_run_at,
                    settings=settings.model_dump(mode="json"),
                )
            )
        return report

    @staticmethod
    def _describe(routine: Routine, settings: Any) -> str:
        if routine.kind == "interval":
            return f"every {settings.check_every_minutes} min"
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        prefix = f"{days[settings.weekday]} " if routine.kind == "weekly" else "daily "
        return f"{prefix}{settings.time} {settings.timezone}"
