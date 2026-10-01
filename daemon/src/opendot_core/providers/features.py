"""Feature switches for opt-in providers (ARCHITECTURE.md 6.2).

Every switch defaults OFF and carries a ``cost_warning`` the UI must show
before the user turns it on. Saving a key never flips a switch.

``paid_fallback_when_plan_runs_out`` does NOT enable automatic switching;
OpenDot never changes providers on its own (see ``ProviderRegistry.fall_back``).
It is only a flag the UI reads to decide whether to OFFER the user a button
such as "Retry this with openai_key", which the user must click per retry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from .registry import ProviderSettings

PAID_PROVIDERS = ("openai_key", "anthropic_key", "openrouter")
OPT_IN_PROVIDERS = (*PAID_PROVIDERS, "local")


class FeatureSpec(BaseModel):
    name: str
    title: str
    cost_warning: str
    requires_any_of: tuple[str, ...]
    """The feature is usable only when at least one of these providers is enabled."""


FEATURES: dict[str, FeatureSpec] = {
    spec.name: spec
    for spec in (
        FeatureSpec(
            name="paid_fallback_when_plan_runs_out",
            title="Use a paid model when the plan runs out",
            cost_warning=(
                "When your ChatGPT plan runs out, OpenDot will offer to retry with a paid provider you choose. "
                "You approve each retry; it is never automatic. Those retries cost real money per token, "
                "up to that provider's monthly spend cap."
            ),
            requires_any_of=PAID_PROVIDERS,
        ),
        FeatureSpec(
            name="claude_as_reviewer",
            title="Use Claude as the reviewer",
            cost_warning=(
                "Risky actions are reviewed by Claude through your Anthropic API key. Each review costs money "
                "per token, up to the anthropic_key monthly spend cap."
            ),
            requires_any_of=("anthropic_key",),
        ),
        FeatureSpec(
            name="smarter_memory_search",
            title="Smarter memory search",
            cost_warning=(
                "Memory search queries are sent to the provider you enabled. A key-based provider costs money "
                "per token, up to its monthly spend cap; a local model is free but needs capable hardware."
            ),
            requires_any_of=("local", *PAID_PROVIDERS),
        ),
    )
}


class FeatureSwitches(BaseModel):
    """Which features the user turned on. Empty by default: everything is off."""

    enabled: set[str] = Field(default_factory=set)

    @field_validator("enabled")
    @classmethod
    def _known(cls, value: set[str]) -> set[str]:
        unknown = value - FEATURES.keys()
        if unknown:
            raise ValueError(f"unknown feature switch: {sorted(unknown)}")
        return value

    def is_on(self, name: str) -> bool:
        return name in self.enabled


def feature_available(name: str, settings: "ProviderSettings") -> bool:
    """True when the switch is on AND a required provider is enabled."""
    spec = FEATURES[name]
    return settings.features.is_on(name) and any(settings.is_enabled(p) for p in spec.requires_any_of)


def paid_retry_offered(settings: "ProviderSettings") -> bool:
    """Flag for the UI: may it offer the user an explicit 'retry with a paid provider' button?

    This never triggers a switch. The retry is a separate, user-initiated call naming one provider.
    """
    return feature_available("paid_fallback_when_plan_runs_out", settings)


def paid_retry_choices(settings: "ProviderSettings") -> list[str]:
    """Enabled paid providers the user could explicitly pick for a retry (empty when the flag is off)."""
    if not paid_retry_offered(settings):
        return []
    return [p for p in PAID_PROVIDERS if settings.is_enabled(p)]


def provider_allowed(use: str, provider: str, settings: "ProviderSettings") -> bool:
    """May an M2 caller send ``use`` ("chat" or "review") requests to ``provider``?

    The plan and a local model need nothing more than being enabled. A paid provider also needs
    the feature switch for that use: "Use Claude as the reviewer" for Anthropic reviews, and the
    paid-provider switch for anything else. Saving or enabling a key alone never allows it.
    """
    if provider not in PAID_PROVIDERS:
        return True
    if use == "review" and provider == "anthropic_key":
        return feature_available("claude_as_reviewer", settings)
    if use == "review":
        return False
    return feature_available("paid_fallback_when_plan_runs_out", settings)
