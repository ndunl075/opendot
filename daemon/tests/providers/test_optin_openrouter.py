import json

import pytest
from optin_support import SECRET, FakeSecrets, Recorder, fixture, make_spend, request

from opendot_core.providers import (
    AuthRequired,
    Completed,
    IncompleteResponse,
    OpenRouterProvider,
    Provider,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    SpendCapReached,
    ToolCall,
)

MODEL = "anthropic/claude-sonnet-5"


def _provider(tmp_path, recorder, cap=5.0):
    return OpenRouterProvider(
        secret_store=FakeSecrets(openrouter_api_key=SECRET),
        transport=recorder.transport,
        spend=make_spend(tmp_path, cap),
    )


def test_identity_and_host(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_openai_text.sse"))
    provider = _provider(tmp_path, recorder)
    assert isinstance(provider, Provider) and provider.name == "openrouter" and provider.paid is True
    events = list(provider.stream(request(MODEL)))
    assert isinstance(events[-1], Completed) and events[-1].text == "Hello"
    sent = recorder.requests[0]
    assert sent.url.host == "openrouter.ai" and sent.url.path == "/api/v1/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {SECRET}"
    assert SECRET not in str(sent.url)
    assert json.loads(sent.content)["usage"] == {"include": True}


def test_tool_call(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_openai_tool.sse"))
    events = list(_provider(tmp_path, recorder).stream(request(MODEL)))
    assert any(isinstance(e, ToolCall) and e.name == "get_weather" for e in events)


def test_incomplete(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_openai_incomplete.sse"))
    with pytest.raises(IncompleteResponse):
        list(_provider(tmp_path, recorder).stream(request(MODEL)))


@pytest.mark.parametrize(
    ("status", "error"), [(401, AuthRequired), (403, AuthRequired), (429, RateLimited), (502, ProviderUnavailable)]
)
def test_http_errors_map(tmp_path, status, error) -> None:
    recorder = Recorder(status=status, json_body={"error": {"message": SECRET}})
    with pytest.raises(error) as caught:
        list(_provider(tmp_path, recorder).stream(request(MODEL)))
    assert SECRET not in caught.value.detail


def test_402_out_of_credits_is_a_provider_error(tmp_path) -> None:
    recorder = Recorder(status=402, json_body={"error": {"message": "no credits"}})
    with pytest.raises(ProviderError) as caught:
        list(_provider(tmp_path, recorder).stream(request(MODEL)))
    assert caught.value.status == 402


def test_cap_zero_fails_closed(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_openai_text.sse"))
    with pytest.raises(SpendCapReached):
        list(_provider(tmp_path, recorder, cap=None).stream(request(MODEL)))
    assert recorder.requests == []


def test_each_provider_has_its_own_cap(tmp_path) -> None:
    spend = make_spend(tmp_path, None)
    spend.set_cap("openai_key", 10.0)
    recorder = Recorder(body=fixture("optin_openai_text.sse"))
    provider = OpenRouterProvider(
        secret_store=FakeSecrets(openrouter_api_key=SECRET), transport=recorder.transport, spend=spend
    )
    with pytest.raises(SpendCapReached):
        list(provider.stream(request(MODEL)))
    assert recorder.requests == []


def test_no_spend_tracker_fails_closed() -> None:
    recorder = Recorder(body=fixture("optin_openai_text.sse"))
    provider = OpenRouterProvider(secret_store=FakeSecrets(openrouter_api_key=SECRET), transport=recorder.transport)
    with pytest.raises(SpendCapReached):
        list(provider.stream(request(MODEL)))
    assert recorder.requests == []


def test_list_models() -> None:
    recorder = Recorder(json_body={"data": [{"id": MODEL, "name": "Claude Sonnet"}]})
    provider = OpenRouterProvider(secret_store=FakeSecrets(openrouter_api_key=SECRET), transport=recorder.transport)
    assert provider.list_models()[0].display_name == "Claude Sonnet"
