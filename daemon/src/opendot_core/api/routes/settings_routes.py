"""Settings endpoints (``settings_get``, ``settings_update``), persisted with ``SettingsStore``.

Keys (see ``settings_store``): ``keep_awake``, ``quiet_hours``, ``tier_overrides``, ``auto_top_tier``,
``style_preset`` and ``providers``. Keep-awake and quiet hours are stored here; other code reads them
from the store. Provider opt-ins update the live ``ProviderRegistry`` settings and persist under
``providers``; a field left out of an update changes nothing, so saving settings never enables a
provider or feature by itself (ARCHITECTURE.md 6.2). Tier overrides and automatic top-tier use are
applied to the live ``Router`` at once (and again at startup by ``build_agent_runtime``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ...providers.features import OPT_IN_PROVIDERS
from ...router.tiers import Job
from ..models import (
    KeepAwakeSettings,
    ModelTierOverride,
    QuietHoursSettings,
    Settings,
    SettingsUpdateRequest,
    StylePreset,
)
from .config_support import apply_tier_overrides, error, ok, parse_body, run, settings_store
from .providers_routes import commit_provider_settings, provider_opt_ins

if TYPE_CHECKING:
    from . import ApiContext

DEFAULT_QUIET_HOURS = QuietHoursSettings(enabled=False, start="22:00", end="07:00", timezone="UTC")
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _invalid_quiet_hours(quiet: QuietHoursSettings) -> str | None:
    if not (_CLOCK.match(quiet.start) and _CLOCK.match(quiet.end)):
        return "quiet_hours: start and end must be times like 22:00"
    try:
        ZoneInfo(quiet.timezone)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return "quiet_hours: unknown timezone"
    if quiet.enabled and quiet.start == quiet.end:
        return "quiet_hours: start and end must differ"
    return None


def _invalid_overrides(overrides: list[ModelTierOverride]) -> str | None:
    known = {job.value for job in Job}
    seen: set[str] = set()
    for item in overrides:
        if item.job_type not in known:
            return f"tier_overrides: unknown job type '{item.job_type}' (use one of {', '.join(sorted(known))})"
        if item.job_type in seen:
            return f"tier_overrides: '{item.job_type}' appears twice"
        seen.add(item.job_type)
    return None


def routes(ctx: ApiContext) -> list[Route]:
    if ctx.database is None or ctx.registry is None:
        return []
    store = settings_store(ctx)
    router = ctx.extras.get("router")

    def current() -> Settings:
        return Settings(
            keep_awake=store.get("keep_awake", KeepAwakeSettings, KeepAwakeSettings(enabled=False)),
            quiet_hours=store.get("quiet_hours", QuietHoursSettings, DEFAULT_QUIET_HOURS),
            providers=provider_opt_ins(ctx),
            tier_overrides=store.get("tier_overrides", list[ModelTierOverride], []),
            auto_top_tier=bool(store.get("auto_top_tier", bool, False)),
            style_preset=store.get("style_preset", StylePreset, "concise"),
        )

    def apply(body: SettingsUpdateRequest) -> Settings:
        if body.keep_awake is not None:
            store.set("keep_awake", body.keep_awake)
        if body.quiet_hours is not None:
            store.set("quiet_hours", body.quiet_hours)
        if body.style_preset is not None:
            store.set("style_preset", body.style_preset)
        if body.tier_overrides is not None:
            store.set("tier_overrides", body.tier_overrides)
            if router is not None:
                apply_tier_overrides(router, body.tier_overrides)
        if body.auto_top_tier is not None:
            store.set("auto_top_tier", body.auto_top_tier)
            if router is not None:
                router.auto_top = body.auto_top_tier
        if body.provider_opt_ins:
            enabled = set(ctx.registry.settings.enabled)
            for name, on in body.provider_opt_ins.items():
                enabled.add(name) if on else enabled.discard(name)
            commit_provider_settings(ctx, enabled=enabled)
        return current()

    async def settings_get(_: Request) -> Response:
        return ok(await run(current))

    async def settings_update(request: Request) -> Response:
        body = await parse_body(request, SettingsUpdateRequest)
        if isinstance(body, Response):
            return body
        problem = None
        if body.quiet_hours is not None:
            problem = _invalid_quiet_hours(body.quiet_hours)
        if problem is None and body.tier_overrides is not None:
            problem = _invalid_overrides(body.tier_overrides)
        if problem is None and body.provider_opt_ins:
            for name, on in body.provider_opt_ins.items():
                if name == "chatgpt_plan" and not on:
                    problem = "provider_opt_ins: the ChatGPT plan is turned off by signing out, not here"
                elif name not in (*OPT_IN_PROVIDERS, "chatgpt_plan"):
                    problem = f"provider_opt_ins: {name} is not an opt-in provider"
                if problem:
                    break
        if problem is not None:
            return error(422, "invalid_request", problem)
        return ok(await run(apply, body))

    return [
        Route("/v1/settings", settings_get, methods=["GET"]),
        Route("/v1/settings", settings_update, methods=["PUT"]),
    ]
