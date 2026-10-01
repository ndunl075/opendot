import pytest

from opendot_core.providers import (
    ChatRequest,
    Completed,
    InputItem,
    ModelInfo,
    Provider,
    ProviderDisabled,
    ProviderRegistry,
    ProviderSettings,
    ProviderSwitchForbidden,
    TextDelta,
    UsageLimitExceeded,
)


class FakeProvider:
    paid = False

    def __init__(self, name: str, *, fail: Exception | None = None) -> None:
        self.name = name
        self.fail = fail
        self.calls = 0

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="m1")]

    def stream(self, request: ChatRequest):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        yield TextDelta(text="hi")
        yield Completed(text="hi", model=request.model)


def _request() -> ChatRequest:
    return ChatRequest(model="m1", input=[InputItem(role="user", content="hello")])


def test_fake_provider_satisfies_the_protocol() -> None:
    assert isinstance(FakeProvider("x"), Provider)


def test_only_the_plan_provider_is_enabled_by_default() -> None:
    assert ProviderSettings().enabled == {"chatgpt_plan"}


def test_a_registered_but_disabled_provider_cannot_be_used() -> None:
    registry = ProviderRegistry()
    registry.register(FakeProvider("openai_key"))

    with pytest.raises(ProviderDisabled):
        registry.get("openai_key")


def test_an_enabled_provider_streams_to_completion() -> None:
    registry = ProviderRegistry(ProviderSettings(enabled={"local"}))
    registry.register(FakeProvider("local"))

    events = list(registry.stream("local", _request()))

    assert [event.type for event in events] == ["text_delta", "completed"]


def test_an_error_propagates_and_no_other_provider_is_tried() -> None:
    plan = FakeProvider("chatgpt_plan", fail=UsageLimitExceeded())
    paid = FakeProvider("openai_key")
    registry = ProviderRegistry(ProviderSettings(enabled={"chatgpt_plan", "openai_key"}))
    registry.register(plan)
    registry.register(paid)

    with pytest.raises(UsageLimitExceeded):
        list(registry.stream("chatgpt_plan", _request()))

    assert plan.calls == 1
    assert paid.calls == 0


def test_explicit_fall_back_is_always_refused() -> None:
    registry = ProviderRegistry()

    with pytest.raises(ProviderSwitchForbidden):
        registry.fall_back("chatgpt_plan", to="openai_key")


def test_errors_carry_a_stable_code_and_a_user_message() -> None:
    error = UsageLimitExceeded("429 from upstream")

    assert error.code == "usage_limit_exceeded"
    assert error.status == 429
    assert "Manage usage" in error.user_message
