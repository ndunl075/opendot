"""Onboarding: persisted state (settings key ``onboarding``), the companion setup and acknowledgements.

Steps, in order: companion, chatgpt, weekly_limit, connections, intro, done. The first three are
derived from facts (a saved companion, a signed-in eligible account, both acknowledgements); the
rest are completed by ``onboarding_complete``, which the UI calls from the connections step.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ..models import (
    API_VERSION,
    CompanionSetupRequest,
    OnboardingAcknowledgeRequest,
    OnboardingState,
    OnboardingStep,
)
from ._common import error, invalid, read_body, reply, settings_for, utcnow
from .chatgpt import current_status
from .companion import DEFAULT_NAME, DEFAULT_SEED, load_record, save_record

if TYPE_CHECKING:
    from . import ApiContext

ONBOARDING_KEY = "onboarding"
_ORDER: tuple[OnboardingStep, ...] = ("companion", "chatgpt", "weekly_limit", "connections", "intro", "done")


class OnboardingRecord(BaseModel):
    weekly_limit_acknowledged: bool = False
    credits_off_acknowledged: bool = False
    completed: bool = False
    intro_message: str | None = None
    completed_at: datetime | None = None


def intro_text(name: str) -> str:
    return (
        f"Hi, I'm {name}. I keep track of what matters, remember what you tell me, and ask before I take any "
        "action in your apps. You can pause me, change my rules or ask me to forget something at any time."
    )


def state_for(ctx: Any) -> OnboardingState:
    settings = settings_for(ctx)
    record = settings.get(ONBOARDING_KEY, OnboardingRecord, OnboardingRecord())
    companion = load_record(settings)
    status = current_status(ctx)
    done: set[OnboardingStep] = set()
    if companion is not None:
        done.add("companion")
    if status.state == "signed_in" and status.eligible:
        done.add("chatgpt")
    if record.weekly_limit_acknowledged and record.credits_off_acknowledged:
        done.add("weekly_limit")
    if record.completed:
        done.update(("connections", "intro", "done"))
    current = next((step for step in _ORDER if step not in done), "done")
    return OnboardingState(
        current_step=current,
        completed_steps=[step for step in _ORDER if step in done],
        companion_name=companion.name if companion else None,
        avatar_seed=companion.avatar_seed if companion else None,
        chatgpt=status,
        weekly_limit_acknowledged=record.weekly_limit_acknowledged,
        credits_off_acknowledged=record.credits_off_acknowledged,
        intro_message=record.intro_message,
    )


def routes(ctx: ApiContext) -> list[Route]:
    settings = settings_for(ctx)
    if settings is None:
        return []

    def load() -> OnboardingRecord:
        return settings.get(ONBOARDING_KEY, OnboardingRecord, OnboardingRecord())

    async def get(_: Request) -> Response:
        return reply(state_for(ctx))

    async def companion(request: Request) -> Response:
        try:
            body: CompanionSetupRequest = await read_body(request, CompanionSetupRequest)
        except ValidationError as problem:
            return invalid(problem)
        name, seed = body.name.strip(), body.avatar_seed.strip()
        if not name or not seed:
            return error(422, "invalid_request", "The name and avatar cannot be blank.")
        save_record(settings, name=name, avatar_seed=seed)
        return reply(state_for(ctx))

    async def acknowledge(request: Request) -> Response:
        try:
            body: OnboardingAcknowledgeRequest = await read_body(request, OnboardingAcknowledgeRequest)
        except ValidationError as problem:
            return invalid(problem)
        if body.credits_off and current_status(ctx).credits_enabled:
            return error(
                409, "credits_enabled",
                "Credit use is still on for apps in ChatGPT. Turn it off, then confirm again.",
            )
        record = load().model_copy(
            update={"weekly_limit_acknowledged": body.weekly_limit_set, "credits_off_acknowledged": body.credits_off}
        )
        settings.set(ONBOARDING_KEY, record)
        return reply(state_for(ctx))

    async def complete(_: Request) -> Response:
        state = state_for(ctx)
        missing = [step for step in ("companion", "chatgpt", "weekly_limit") if step not in state.completed_steps]
        if missing:
            return error(409, "onboarding_incomplete", f"Finish these steps first: {', '.join(missing)}.")
        record = load()
        if not record.completed:
            name = state.companion_name or DEFAULT_NAME
            record = record.model_copy(
                update={"completed": True, "intro_message": intro_text(name), "completed_at": utcnow()}
            )
            settings.set(ONBOARDING_KEY, record)
        return reply(state_for(ctx))

    v = f"/{API_VERSION}/onboarding"
    return [
        Route(v, get, methods=["GET"]),
        Route(f"{v}/companion", companion, methods=["POST"]),
        Route(f"{v}/acknowledge", acknowledge, methods=["POST"]),
        Route(f"{v}/complete", complete, methods=["POST"]),
    ]


__all__ = ["DEFAULT_SEED", "OnboardingRecord", "routes", "state_for"]
