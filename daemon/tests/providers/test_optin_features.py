import pytest
from optin_support import FakeSecrets, Recorder, fixture, make_spend, request

from opendot_core.providers import (
    DEFAULT_PROVIDER,
    FEATURES,
    FeatureSwitches,
    ProviderDisabled,
    ProviderError,
    ProviderSettings,
    RateLimited,
    SpendCapReached,
    build_registry,
    paid_retry_choices,
    paid_retry_offered,
)
from opendot_core.providers.features import feature_available

ALL_KEYS = FakeSecrets(openai_api_key="k1", anthropic_api_key="k2", openrouter_api_key="k3")


def test_all_switches_default_off_with_cost_warnings() -> None:
    assert set(FEATURES) == {"paid_fallback_when_plan_runs_out", "claude_as_reviewer", "smarter_memory_search"}
    settings = ProviderSettings()
    assert settings.features.enabled == set()
    for name, spec in FEATURES.items():
        assert spec.cost_warning and not settings.features.is_on(name)
        assert not feature_available(name, settings)


def test_existing_default_is_unchanged() -> None:
    assert ProviderSettings().enabled == {DEFAULT_PROVIDER}


def test_unknown_switch_rejected() -> None:
    with pytest.raises(ValueError):
        FeatureSwitches(enabled={"auto_switch"})


def test_saving_a_key_enables_nothing() -> None:
    registry = build_registry(ProviderSettings(), ALL_KEYS)
    assert registry.names() == []
    for name in ("openai_key", "anthropic_key", "openrouter", "local"):
        with pytest.raises(ProviderDisabled):
            registry.get(name)


def test_enabled_without_a_key_is_not_registered() -> None:
    settings = ProviderSettings(enabled={DEFAULT_PROVIDER, "openai_key", "anthropic_key"})
    registry = build_registry(settings, FakeSecrets(openai_api_key="k1"))
    assert registry.names() == ["openai_key"]


def test_local_needs_no_key_but_must_be_enabled() -> None:
    assert build_registry(ProviderSettings(), FakeSecrets()).names() == []
    registry = build_registry(ProviderSettings(enabled={"local"}), FakeSecrets())
    assert registry.get("local").paid is False


def test_enabled_paid_provider_with_default_cap_still_refuses(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_openai_text.sse"))
    registry = build_registry(
        ProviderSettings(enabled={"openai_key"}), ALL_KEYS, recorder.transport, spend=make_spend(tmp_path, None)
    )
    with pytest.raises(SpendCapReached):
        list(registry.stream("openai_key", request()))
    assert recorder.requests == []


def test_registry_never_switches_providers(tmp_path) -> None:
    recorder = Recorder(status=429, json_body={"error": {"message": "slow"}})
    settings = ProviderSettings(enabled={"openai_key", "openrouter"})
    registry = build_registry(settings, ALL_KEYS, recorder.transport, spend=make_spend(tmp_path, 5.0))
    with pytest.raises(RateLimited):
        list(registry.stream("openai_key", request()))
    assert len(recorder.requests) == 1


def test_claude_reviewer_requires_anthropic_key_provider() -> None:
    on = FeatureSwitches(enabled={"claude_as_reviewer"})
    assert not feature_available("claude_as_reviewer", ProviderSettings(features=on))
    assert not feature_available("claude_as_reviewer", ProviderSettings(features=on, enabled={"openai_key"}))
    assert feature_available("claude_as_reviewer", ProviderSettings(features=on, enabled={"anthropic_key"}))


def test_smarter_memory_search_accepts_local_or_a_key() -> None:
    on = FeatureSwitches(enabled={"smarter_memory_search"})
    assert not feature_available("smarter_memory_search", ProviderSettings(features=on))
    assert feature_available("smarter_memory_search", ProviderSettings(features=on, enabled={"local"}))
    assert feature_available("smarter_memory_search", ProviderSettings(features=on, enabled={"openrouter"}))


def test_paid_fallback_is_only_a_ui_flag_for_explicit_retry() -> None:
    off = ProviderSettings(enabled={"openai_key"})
    assert not paid_retry_offered(off) and paid_retry_choices(off) == []
    on = ProviderSettings(
        enabled={DEFAULT_PROVIDER, "openai_key", "local"},
        features=FeatureSwitches(enabled={"paid_fallback_when_plan_runs_out"}),
    )
    assert paid_retry_offered(on)
    assert paid_retry_choices(on) == ["openai_key"]  # the user picks one; nothing switches by itself
    with pytest.raises(ProviderError):
        build_registry(on, ALL_KEYS).fall_back(DEFAULT_PROVIDER, to="openai_key")
