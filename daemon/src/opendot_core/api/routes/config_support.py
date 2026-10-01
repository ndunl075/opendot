"""Helpers shared by the rules, usage, settings, providers and backup route modules.

Everything an endpoint needs beyond the contract models lives on ``ApiContext.extras`` (set by
``agent.runtime.build_agent_runtime``); each helper falls back to a sensible default so a module
also works with a bare context (unit tests):

- ``settings_store``: ``opendot_core.settings_store.SettingsStore``
- ``secret_store``: an OS-keychain ``SecretStore`` (default ``SystemKeyringSecretStore``)
- ``spend``: ``providers.spend.SpendTracker``
- ``router``: the live ``router.Router``
- ``provider_transport``: optional ``httpx`` transport handed to providers built on enable (tests)
- ``backup_dir``: where encrypted backups live (default ``<database folder>/backups``)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from ...providers.factory import _PROVIDER_CLASSES
from ...providers.registry import DEFAULT_PROVIDER, ProviderRegistry, ProviderSettings
from ...providers.spend import SpendTracker
from ...router import Router
from ...router.tiers import FAMILY_TIERS, Job, Tier
from ...secret_store import SecretStore, SystemKeyringSecretStore
from ...settings_store import SettingsStore
from ..models import ErrorResponse, ModelTierOverride

if TYPE_CHECKING:
    from . import ApiContext

M = TypeVar("M", bound=BaseModel)

#: Contract tier names (model families) to the router's tiers.
TIER_BY_FAMILY: dict[str, Tier] = dict(FAMILY_TIERS)


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(ErrorResponse(code=code, message=message).model_dump(mode="json"), status_code=status)


async def parse_body(request: Request, model: type[M]) -> M | JSONResponse:
    """The validated body, or a 422 ``ErrorResponse``. The message never echoes the input (it may
    hold an API key)."""
    raw = await request.body()
    try:
        return model.model_validate_json(raw if raw.strip() else b"{}")
    except ValidationError as failure:
        first = failure.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        return error(422, "invalid_request", f"{where}: {first['msg']}" if where else str(first["msg"]))


def ok(model: BaseModel, status: int = 200) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), status_code=status)


async def run(function: Any, *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(function, *args, **kwargs)


# -- shared services ---------------------------------------------------------------------------


def settings_store(ctx: ApiContext) -> SettingsStore:
    store = ctx.extras.get("settings_store")
    if store is None:
        store = ctx.extras["settings_store"] = SettingsStore(ctx.database)
    return store


def secret_store(ctx: ApiContext) -> SecretStore:
    store = ctx.extras.get("secret_store")
    if store is None:
        store = ctx.extras["secret_store"] = SystemKeyringSecretStore()
    return store


def spend_tracker(ctx: ApiContext) -> SpendTracker:
    tracker = ctx.extras.get("spend")
    if tracker is None:
        tracker = ctx.extras["spend"] = SpendTracker(ctx.database)
    return tracker


def has_secret(store: SecretStore, name: str) -> bool:
    """Whether a non-empty secret is stored. Any keychain failure counts as "not stored"."""
    try:
        return bool(store.get_required(name).strip())
    except Exception:  # noqa: BLE001 - a missing or broken keychain must not break a settings read
        return False


# -- providers: persisted settings and the live registry ---------------------------------------


def persist_providers(ctx: ApiContext) -> None:
    settings_store(ctx).set("providers", ctx.registry.settings)


def register_enabled(
    registry: ProviderRegistry, secrets: SecretStore, spend: SpendTracker, transport: Any = None
) -> None:
    """Register the opt-in providers that are enabled and have what they need (the same rule as
    ``providers.factory.build_registry``). Never enables anything and never replaces a provider."""
    for name, cls in _PROVIDER_CLASSES.items():
        if name in registry.names() or not registry.settings.is_enabled(name):
            continue
        if cls.secret_name is not None and not cls.key_optional and not has_secret(secrets, cls.secret_name):
            continue
        registry.register(cls(secret_store=secrets, transport=transport, spend=spend))


def sync_registry(ctx: ApiContext) -> None:
    register_enabled(
        ctx.registry, secret_store(ctx), spend_tracker(ctx), ctx.extras.get("provider_transport")
    )


def load_provider_settings(registry: ProviderRegistry, store: SettingsStore) -> bool:
    """Copy persisted provider opt-ins and feature switches into the live registry settings."""
    saved = store.get("providers", ProviderSettings, None)
    if saved is None:
        return False
    registry.settings.enabled = set(saved.enabled) | {DEFAULT_PROVIDER}
    registry.settings.features = saved.features
    return True


# -- router: tier overrides and automatic top-tier use -----------------------------------------


def apply_tier_overrides(router: Router, overrides: list[ModelTierOverride]) -> None:
    known = {job.value for job in Job}
    router.job_overrides = {
        Job(item.job_type): (TIER_BY_FAMILY[item.tier], item.effort) for item in overrides if item.job_type in known
    }


def load_router_settings(router: Router, store: SettingsStore) -> None:
    apply_tier_overrides(router, store.get("tier_overrides", list[ModelTierOverride], []))
    router.auto_top = bool(store.get("auto_top_tier", bool, False))
