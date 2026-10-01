"""Opt-in provider endpoints: enablement, API keys, feature switches, spend caps (ARCHITECTURE.md 6.2).

- Enabling a provider and turning a feature on are separate, explicit calls. Saving a key changes
  neither, and no endpoint here enables anything as a side effect.
- API keys go only to the OS keychain (``secret_store``). They are never stored in SQLite, logged or
  returned: the only answer is ``ProviderKeyStatus.key_saved``, a boolean.
- Enablement and feature switches are written to the ``providers`` setting first, then applied to
  the live ``ProviderRegistry`` settings (and the provider is registered once it can run).
- Spend caps and month-to-date spend come from ``providers.spend.SpendTracker`` (cap 0 fails closed).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ...providers.factory import _PROVIDER_CLASSES
from ...providers.features import FEATURES, OPT_IN_PROVIDERS, PAID_PROVIDERS, FeatureSwitches, feature_available
from ...providers.registry import DEFAULT_PROVIDER, ProviderSettings
from ..models import (
    FeatureSwitchList,
    FeatureSwitchSetRequest,
    FeatureSwitchState,
    ProviderApiKeyRequest,
    ProviderEnabledRequest,
    ProviderKeyStatus,
    ProviderOptIn,
    ProviderSpend,
    ProviderSpendCapRequest,
    ProviderSpendList,
)
from .config_support import (
    error,
    has_secret,
    ok,
    parse_body,
    run,
    secret_store,
    settings_store,
    spend_tracker,
    sync_registry,
)

if TYPE_CHECKING:
    from . import ApiContext

_LOCK = threading.Lock()

PROVIDER_LABELS: dict[str, tuple[str, str | None]] = {
    DEFAULT_PROVIDER: ("ChatGPT plan", None),
    "openai_key": (
        "OpenAI API key",
        "Pay per token on your own OpenAI account, up to the monthly spend cap you set here. "
        "Off until you turn it on; saving a key does not turn it on.",
    ),
    "anthropic_key": (
        "Anthropic API key",
        "Pay per token on your own Anthropic account, up to the monthly spend cap you set here. "
        "Off until you turn it on; saving a key does not turn it on.",
    ),
    "openrouter": (
        "OpenRouter key",
        "Pay per token through OpenRouter, up to the monthly spend cap you set here. Your prompts go to "
        "OpenRouter and the model maker it routes to. Off until you turn it on.",
    ),
    "local": (
        "Local model",
        "Free, but needs capable hardware. Your prompts go to the local server you point OpenDot at.",
    ),
}


def provider_opt_ins(ctx: ApiContext) -> list[ProviderOptIn]:
    """Every provider with its switch, cost warning and whether it is configured (a key is saved)."""
    settings = ctx.registry.settings
    secrets = secret_store(ctx)
    items: list[ProviderOptIn] = []
    for name, (label, warning) in PROVIDER_LABELS.items():
        cls = _PROVIDER_CLASSES.get(name)
        if name == DEFAULT_PROVIDER:
            configured = name in ctx.registry.names()
        elif cls is not None and cls.secret_name is not None and not cls.key_optional:
            configured = has_secret(secrets, cls.secret_name)
        else:
            configured = True  # a local model needs no key
        items.append(
            ProviderOptIn(
                provider=name,  # type: ignore[arg-type]
                label=label,
                enabled=settings.is_enabled(name),
                cost_warning=warning,
                configured=configured,
                feature_switches=[spec.name for spec in FEATURES.values() if name in spec.requires_any_of],
            )
        )
    return items


def commit_provider_settings(
    ctx: ApiContext, *, enabled: set[str] | None = None, features: set[str] | None = None
) -> None:
    """Persist the new provider settings (key ``providers``), then apply them to the live registry."""
    with _LOCK:
        live: ProviderSettings = ctx.registry.settings
        new = ProviderSettings(
            enabled=(set(live.enabled) if enabled is None else enabled) | {DEFAULT_PROVIDER},
            features=FeatureSwitches(enabled=set(live.features.enabled) if features is None else features),
        )
        settings_store(ctx).set("providers", new)
        live.enabled = new.enabled
        live.features = new.features
    sync_registry(ctx)


def _feature_state(ctx: ApiContext, name: str) -> FeatureSwitchState:
    spec = FEATURES[name]
    settings = ctx.registry.settings
    return FeatureSwitchState(
        name=name,  # type: ignore[arg-type]
        title=spec.title,
        enabled=settings.features.is_on(name),
        available=feature_available(name, settings),
        cost_warning=spec.cost_warning,
        requires_any_of=list(spec.requires_any_of),  # type: ignore[arg-type]
        requirement="Needs one of these turned on: " + ", ".join(spec.requires_any_of) + ".",
    )


def routes(ctx: ApiContext) -> list[Route]:
    if ctx.database is None or ctx.registry is None:
        return []

    def check(provider: str, allowed: tuple[str, ...], what: str) -> Response | None:
        if provider in allowed:
            return None
        if provider in PROVIDER_LABELS:
            return error(422, "invalid_provider", f"{provider} has no {what}.")
        return error(404, "not_found", "No such provider.")

    def key_status(provider: str) -> ProviderKeyStatus:
        cls = _PROVIDER_CLASSES[provider]
        return ProviderKeyStatus(
            provider=provider,  # type: ignore[arg-type]
            key_saved=has_secret(secret_store(ctx), str(cls.secret_name)),
        )

    def spend_for(provider: str) -> ProviderSpend:
        tracker = spend_tracker(ctx)
        cap = tracker.get_cap(provider)
        return ProviderSpend(
            provider=provider,  # type: ignore[arg-type]
            cap_usd=cap,
            spent_usd=tracker.spent(provider),
            month=tracker.month(),
            cap_set=cap > 0,
        )

    async def provider_enabled_set(request: Request) -> Response:
        provider = request.path_params["provider"]
        if (refused := check(provider, OPT_IN_PROVIDERS, "opt-in switch")) is not None:
            return refused
        body = await parse_body(request, ProviderEnabledRequest)
        if isinstance(body, Response):
            return body

        def apply() -> ProviderOptIn:
            enabled = set(ctx.registry.settings.enabled)
            enabled.add(provider) if body.enabled else enabled.discard(provider)
            commit_provider_settings(ctx, enabled=enabled)
            return next(item for item in provider_opt_ins(ctx) if item.provider == provider)

        return ok(await run(apply))

    async def api_key_save(request: Request) -> Response:
        provider = request.path_params["provider"]
        if (refused := check(provider, PAID_PROVIDERS, "API key")) is not None:
            return refused
        body = await parse_body(request, ProviderApiKeyRequest)
        if isinstance(body, Response):
            return body
        key = body.api_key.strip()
        if not key:
            return error(422, "invalid_request", "api_key: a key cannot be blank")

        def save() -> ProviderKeyStatus:
            store = secret_store(ctx)
            store.store(str(_PROVIDER_CLASSES[provider].secret_name), key)  # type: ignore[attr-defined]
            sync_registry(ctx)  # registers an already-enabled provider; enables nothing
            return key_status(provider)

        try:
            return ok(await run(save))
        except Exception:  # noqa: BLE001 - never put the key (or the keychain's message) in a response
            return error(503, "keychain_unavailable", "The OS keychain could not store the key.")

    async def api_key_remove(request: Request) -> Response:
        provider = request.path_params["provider"]
        if (refused := check(provider, PAID_PROVIDERS, "API key")) is not None:
            return refused

        def remove() -> ProviderKeyStatus:
            secret_store(ctx).delete(str(_PROVIDER_CLASSES[provider].secret_name))  # type: ignore[attr-defined]
            return key_status(provider)

        try:
            return ok(await run(remove))
        except Exception:  # noqa: BLE001
            return error(503, "keychain_unavailable", "The OS keychain could not remove the key.")

    async def features_list(_: Request) -> Response:
        return ok(FeatureSwitchList(features=[_feature_state(ctx, name) for name in FEATURES]))

    async def feature_set(request: Request) -> Response:
        name = request.path_params["feature"]
        if name not in FEATURES:
            return error(404, "not_found", "No such feature switch.")
        body = await parse_body(request, FeatureSwitchSetRequest)
        if isinstance(body, Response):
            return body

        def apply() -> FeatureSwitchState:
            on = set(ctx.registry.settings.features.enabled)
            on.add(name) if body.enabled else on.discard(name)
            commit_provider_settings(ctx, features=on)  # turns on the switch only, never a provider
            return _feature_state(ctx, name)

        return ok(await run(apply))

    async def spend_get(_: Request) -> Response:
        return ok(ProviderSpendList(providers=await run(lambda: [spend_for(p) for p in PAID_PROVIDERS])))

    async def spend_cap_set(request: Request) -> Response:
        provider = request.path_params["provider"]
        if (refused := check(provider, PAID_PROVIDERS, "spend cap")) is not None:
            return refused
        body = await parse_body(request, ProviderSpendCapRequest)
        if isinstance(body, Response):
            return body

        def apply() -> ProviderSpend:
            spend_tracker(ctx).set_cap(provider, body.cap_usd)
            return spend_for(provider)

        return ok(await run(apply))

    v = "/v1/providers"
    return [
        # fixed segments first, so "features" and "spend" are never read as a provider name
        Route(f"{v}/features", features_list, methods=["GET"]),
        Route(f"{v}/features/{{feature}}", feature_set, methods=["PUT"]),
        Route(f"{v}/spend", spend_get, methods=["GET"]),
        Route(f"{v}/{{provider}}/enabled", provider_enabled_set, methods=["PUT"]),
        Route(f"{v}/{{provider}}/api-key", api_key_save, methods=["PUT"]),
        Route(f"{v}/{{provider}}/api-key", api_key_remove, methods=["DELETE"]),
        Route(f"{v}/{{provider}}/spend-cap", spend_cap_set, methods=["PUT"]),
    ]
