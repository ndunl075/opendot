"""Companion identity (name, avatar), its task lists, reset, rename and avatar re-roll.

``GET /v1/companion`` itself stays in ``api.server`` (it also serves pause and resume); it reads the
persisted identity through ``companion_identity`` below. The identity lives in the settings store
under the key ``companion``; ``onboarding_companion`` writes the same record.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ...agent.loop_api import TaskState
from ...audit import AuditEvent, AuditLog
from ...policy import ApprovalService, PolicyError
from ...scheduled_tasks import ScheduledTaskStore
from ...settings_store import SettingsStore
from ..models import (
    API_VERSION,
    CompanionAvatarRequest,
    CompanionProfile,
    CompanionRenameRequest,
    CompanionResetRequest,
    CompanionTask,
    CompanionTaskList,
    StylePreset,
)
from ._common import error, invalid, parse_time, read_body, reply, settings_for, utcnow

if TYPE_CHECKING:
    from . import ApiContext

COMPANION_KEY = "companion"
DEFAULT_NAME = "OpenDot"
DEFAULT_SEED = "default"
_IN_PROGRESS = frozenset(
    {
        TaskState.RUNNING,
        TaskState.WAITING_APPROVAL,
        TaskState.WAITING_TOP_TIER,
        TaskState.PAUSED_TASK_BUDGET,
        TaskState.PAUSED_DAILY_BUDGET,
        TaskState.PAUSED_PLAN_LIMIT,
        TaskState.PAUSED,
    }
)
_COMPLETED_LIMIT = 50


class CompanionRecord(BaseModel):
    name: str
    avatar_seed: str
    created_at: datetime


def load_record(settings: SettingsStore | None) -> CompanionRecord | None:
    return None if settings is None else settings.get(COMPANION_KEY, CompanionRecord, None)


def save_record(settings: SettingsStore, *, name: str | None = None, avatar_seed: str | None = None) -> CompanionRecord:
    existing = load_record(settings)
    record = CompanionRecord(
        name=(name if name is not None else (existing.name if existing else DEFAULT_NAME)),
        avatar_seed=(avatar_seed if avatar_seed is not None else (existing.avatar_seed if existing else DEFAULT_SEED)),
        created_at=existing.created_at if existing else utcnow(),
    )
    settings.set(COMPANION_KEY, record)
    return record


def companion_identity(
    settings: SettingsStore | None, default_name: str, default_created: datetime
) -> tuple[str, str, datetime, StylePreset]:
    """The persisted name, avatar seed, creation time and style preset (defaults when never saved)."""
    record = load_record(settings)
    style: StylePreset = "concise"
    if settings is not None:
        style = settings.get("style_preset", StylePreset, "concise")  # type: ignore[arg-type]
    if record is None:
        return default_name, DEFAULT_SEED, default_created, style
    return record.name, record.avatar_seed, record.created_at, style


def _trim(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _tasks(ctx: Any) -> CompanionTaskList:
    meter = ctx.meter
    in_progress: list[CompanionTask] = []
    completed: list[CompanionTask] = []
    with ctx.database.connect() as connection:
        rows = connection.execute("SELECT * FROM agent_tasks ORDER BY created_at DESC, id DESC").fetchall()
        jobs = connection.execute(
            "SELECT id, kind, next_run_at, payload_json, created_at FROM jobs "
            "WHERE state = 'active' AND next_run_at IS NOT NULL ORDER BY next_run_at ASC"
        ).fetchall()
    for row in rows:
        state = TaskState(row["state"])
        credits = float(meter.credits_for_task(row["id"])) if meter is not None else 0.0
        done = state not in _IN_PROGRESS
        if done and len(completed) >= _COMPLETED_LIMIT:
            continue
        task = CompanionTask(
            id=row["id"],
            title=_trim(row["message"], 80) or "Task",
            status="completed" if done else "in_progress",
            job_type=row["job_type"],
            created_at=parse_time(row["created_at"]),  # type: ignore[arg-type]
            completed_at=parse_time(row["updated_at"]) if done else None,
            credits_used=credits,
            summary=(_trim(row["final_text"] or row["detail"] or "", 200) or None),
        )
        (completed if done else in_progress).append(task)
    scheduled: list[CompanionTask] = []
    for job in jobs:
        try:
            payload = json.loads(job["payload_json"])
        except ValueError:
            payload = {}
        text = ""
        if isinstance(payload, dict):
            text = str(payload.get("prompt") or payload.get("message") or payload.get("text") or "")
        scheduled.append(
            CompanionTask(
                id=job["id"],
                title=_trim(text, 80) or str(job["kind"]).replace("_", " ").capitalize(),
                status="scheduled",
                job_type=str(job["kind"]),
                created_at=parse_time(job["created_at"]),  # type: ignore[arg-type]
                scheduled_for=parse_time(job["next_run_at"]),
            )
        )
    return CompanionTaskList(in_progress=in_progress, scheduled=scheduled, completed=completed)


def _reset(ctx: Any, forget_memory: bool) -> None:
    """Clear conversations and task state. The audit log (``tool_runs``) is append-only and untouched."""
    approvals = ApprovalService(ctx.database)
    with ctx.database.connect() as connection:
        waiting = connection.execute(
            "SELECT DISTINCT approval_id FROM agent_steps WHERE approval_id IS NOT NULL AND task_id IN "
            "(SELECT id FROM agent_tasks)"
        ).fetchall()
    for row in waiting:
        try:
            approvals.reject(row["approval_id"], actor=ctx.actor)
        except PolicyError:
            pass  # already decided or expired
    for job in ScheduledTaskStore.pending(ctx.database):
        ScheduledTaskStore.cancel(ctx.database, job.id)
    with ctx.database.connect() as connection:
        with ctx.database.transaction(connection):
            connection.execute("DELETE FROM conversation_messages")
            connection.execute("DELETE FROM conversations")
            connection.execute("DELETE FROM agent_history")
            connection.execute("DELETE FROM agent_steps")
            connection.execute("DELETE FROM agent_tasks")
            AuditLog.append_in_transaction(
                connection,
                AuditEvent(
                    actor=ctx.actor,
                    client="api",
                    tool="companion_reset",
                    outcome="ok",
                    result={"forget_memory": forget_memory},
                ),
            )
    reset_hub = getattr(ctx.hub, "reset", None)
    if callable(reset_hub):
        reset_hub()
    if forget_memory:
        from ...memory_graph import MemoryGraph

        graph = MemoryGraph(ctx.database)
        with ctx.database.connect() as connection:
            ids = [r["id"] for r in connection.execute("SELECT id FROM memories WHERE status != 'deleted'").fetchall()]
        for memory_id in ids:
            graph.forget_memory(memory_id, reason="companion reset", actor=ctx.actor)


def routes(ctx: ApiContext) -> list[Route]:
    if ctx.database is None:
        return []
    settings = settings_for(ctx)
    assert settings is not None
    default_name = ctx.extras.get("companion_name", DEFAULT_NAME)
    started = utcnow()

    def profile() -> CompanionProfile:
        shared = ctx.extras.get("companion_profile")  # the server's own, so every endpoint answers alike
        if callable(shared):
            return shared()
        name, seed, created, style = companion_identity(settings, default_name, started)
        reason = ctx.loop.paused()
        return CompanionProfile(
            name=name, avatar_seed=seed, paused=reason is not None, paused_reason=reason, created_at=created,
            style_preset=style,
        )

    async def tasks(_: Request) -> Response:
        return reply(await asyncio.to_thread(_tasks, ctx))

    async def reset(request: Request) -> Response:
        try:
            body: CompanionResetRequest = await read_body(request, CompanionResetRequest)
        except ValidationError as problem:
            return invalid(problem)
        await asyncio.to_thread(_reset, ctx, body.forget_memory)
        return reply(profile())

    async def rename(request: Request) -> Response:
        try:
            body: CompanionRenameRequest = await read_body(request, CompanionRenameRequest)
        except ValidationError as problem:
            return invalid(problem)
        name = body.name.strip()
        if not name:
            return error(422, "invalid_request", "The name cannot be blank.")
        save_record(settings, name=name)
        return reply(profile())

    async def avatar(request: Request) -> Response:
        try:
            body: CompanionAvatarRequest = await read_body(request, CompanionAvatarRequest)
        except ValidationError as problem:
            return invalid(problem)
        seed = (body.avatar_seed or "").strip() or f"seed-{secrets.token_hex(4)}"
        if len(seed) > 64:
            return error(422, "invalid_request", "The avatar seed is too long.")
        save_record(settings, avatar_seed=seed)
        return reply(profile())

    v = f"/{API_VERSION}/companion"
    return [
        Route(f"{v}/tasks", tasks, methods=["GET"]),
        Route(f"{v}/reset", reset, methods=["POST"]),
        Route(f"{v}/rename", rename, methods=["POST"]),
        Route(f"{v}/avatar", avatar, methods=["POST"]),
    ]
