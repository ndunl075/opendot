"""Build a registry of the opt-in providers the user actually turned on.

A provider is registered only when (1) the user enabled it in
``ProviderSettings.enabled`` AND (2) its key exists in the keychain (``local``
needs no key). Saving a key never enables anything, and an enabled provider
with no key is simply not registered. ``chatgpt_plan`` is not built here; its
owner registers it. Paid providers also get the shared ``SpendTracker``; with
none, they refuse every call (fail closed).
"""

from __future__ import annotations

import httpx

from ..secret_store import SecretStore, SecretStoreError
from .anthropic_key import AnthropicKeyProvider
from .local import LocalProvider
from .openai_key import OpenAIKeyProvider
from .openrouter import OpenRouterProvider
from .registry import ProviderRegistry, ProviderSettings
from .spend import SpendTracker

_PROVIDER_CLASSES = {
    "openai_key": OpenAIKeyProvider,
    "anthropic_key": AnthropicKeyProvider,
    "openrouter": OpenRouterProvider,
    "local": LocalProvider,
}


def _has_key(secret_store: SecretStore, name: str) -> bool:
    try:
        return bool(secret_store.get_required(name).strip())
    except SecretStoreError:
        return False


def build_registry(
    settings: ProviderSettings,
    secret_store: SecretStore,
    transport: httpx.BaseTransport | None = None,
    *,
    spend: SpendTracker | None = None,
) -> ProviderRegistry:
    registry = ProviderRegistry(settings)
    for name, cls in _PROVIDER_CLASSES.items():
        if not settings.is_enabled(name):
            continue
        if cls.secret_name is not None and not cls.key_optional and not _has_key(secret_store, cls.secret_name):
            continue
        registry.register(cls(secret_store=secret_store, transport=transport, spend=spend))
    return registry
