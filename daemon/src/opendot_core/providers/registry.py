"""Provider registry with feature switches and the never-switch-on-error rule.

Saving a key never turns anything on (ARCHITECTURE.md section 6.2): a provider
is usable only when it is registered *and* enabled. Callers name the provider
they want; the registry never picks another one, and ``call`` re-raises every
``ProviderError`` unchanged instead of retrying somewhere else.
"""

from __future__ import annotations

from typing import Callable, Iterator, TypeVar

from pydantic import BaseModel, Field

from .base import Provider
from .errors import ProviderDisabled, ProviderError, ProviderSwitchForbidden
from .features import FeatureSwitches
from .types import ChatRequest, StreamEvent

#: The only provider that is on without the user turning it on.
DEFAULT_PROVIDER = "chatgpt_plan"

T = TypeVar("T")


class ProviderSettings(BaseModel):
    """Which providers the user has switched on. Everything else is off."""

    enabled: set[str] = Field(default_factory=lambda: {DEFAULT_PROVIDER})
    features: FeatureSwitches = Field(default_factory=FeatureSwitches)
    """Feature switches (all off by default). Saving a key changes neither field."""

    def is_enabled(self, name: str) -> bool:
        return name in self.enabled


class ProviderRegistry:
    def __init__(self, settings: ProviderSettings | None = None) -> None:
        self.settings = settings or ProviderSettings()
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        self._providers[provider.name] = provider

    def names(self) -> list[str]:
        return sorted(self._providers)

    def get(self, name: str) -> Provider:
        """Return an enabled provider by name, or raise ``ProviderDisabled``."""
        provider = self._providers.get(name)
        if provider is None or not self.settings.is_enabled(name):
            raise ProviderDisabled(f"provider {name!r} is not enabled")
        return provider

    def stream(self, name: str, request: ChatRequest) -> Iterator[StreamEvent]:
        """Stream from exactly the named provider. Errors propagate; nothing falls back."""
        return self.get(name).stream(request)

    def call(self, name: str, operation: Callable[[Provider], T]) -> T:
        """Run ``operation`` against the named provider and let any error through."""
        return operation(self.get(name))

    def fall_back(self, failed: str, *, to: str) -> None:
        """Always refuses. Exists so code that wants to switch providers fails loudly and testably."""
        raise ProviderSwitchForbidden(f"refusing to switch from {failed!r} to {to!r} after an error")


__all__ = ["DEFAULT_PROVIDER", "ProviderError", "ProviderRegistry", "ProviderSettings"]
