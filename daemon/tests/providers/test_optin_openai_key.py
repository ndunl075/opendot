import logging

import httpx
import pytest
from optin_support import SECRET, FakeSecrets, Recorder, fixture, make_spend, request

from opendot_core.providers import (
    AuthRequired,
    Completed,
    IncompleteResponse,
    OpenAIKeyProvider,
    Provider,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    SpendCapReached,
    TextDelta,
    ToolCall,
)


def _provider(tmp_path, recorder, cap=5.0, secrets=None):
    return OpenAIKeyProvider(
        secret_store=secrets or FakeSecrets(openai_api_key=SECRET),
        transport=recorder.transport,
        spend=make_spend(tmp_path, cap),
    )


def test_is_a_paid_provider() -> None:
    provider = OpenAIKeyProvider(secret_store=FakeSecrets())
    assert isinstance(provider, Provider)
    assert provider.name == "openai_key" and provider.paid is True


def test_happy_stream(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_openai_text.sse"))
    events = list(_provider(tmp_path, recorder).stream(request()))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
    done = events[-1]
    assert isinstance(done, Completed) and done.text == "Hello"
    assert done.usage.input_tokens == 100 and done.usage.output_tokens == 20
    assert done.usage.cached_input_tokens == 10 and done.usage.reasoning_tokens == 5
    sent = recorder.requests[0]
    assert sent.url.host == "api.openai.com"
    assert sent.headers["authorization"] == f"Bearer {SECRET}"
    assert SECRET not in str(sent.url)


def test_tool_call(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_openai_tool.sse"))
    events = list(_provider(tmp_path, recorder).stream(request()))
    call = next(e for e in events if isinstance(e, ToolCall))
    assert (call.call_id, call.name, call.arguments) == ("call_1", "get_weather", '{"city":"Oslo"}')
    assert isinstance(events[-1], Completed) and events[-1].tool_calls == [call]


def test_incomplete_stream_is_not_success(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_openai_incomplete.sse"))
    with pytest.raises(IncompleteResponse):
        list(_provider(tmp_path, recorder).stream(request()))


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, AuthRequired),
        (403, AuthRequired),
        (429, RateLimited),
        (500, ProviderUnavailable),
        (503, ProviderUnavailable),
    ],
)
def test_http_errors_map(tmp_path, status, error) -> None:
    recorder = Recorder(status=status, json_body={"error": {"message": f"bad {SECRET}"}})
    with pytest.raises(error) as caught:
        list(_provider(tmp_path, recorder).stream(request()))
    assert caught.value.status == status
    assert SECRET not in str(caught.value) and SECRET not in caught.value.detail
    assert len(recorder.requests) == 1  # never retried, never another provider


def test_network_failure_is_unavailable(tmp_path) -> None:
    def boom(_request):
        raise httpx.ConnectError("down")

    provider = OpenAIKeyProvider(
        secret_store=FakeSecrets(openai_api_key=SECRET),
        transport=httpx.MockTransport(boom),
        spend=make_spend(tmp_path, 5.0),
    )
    with pytest.raises(ProviderUnavailable):
        list(provider.stream(request()))


def test_missing_key_requires_auth_without_network(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_openai_text.sse"))
    with pytest.raises(AuthRequired):
        list(_provider(tmp_path, recorder, secrets=FakeSecrets()).stream(request()))
    assert recorder.requests == []


def test_cap_zero_fails_closed_before_any_request(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_openai_text.sse"))
    with pytest.raises(SpendCapReached):
        list(_provider(tmp_path, recorder, cap=None).stream(request()))
    assert recorder.requests == []


def test_spend_is_recorded_and_the_cap_then_trips(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_openai_text.sse"))
    provider = _provider(tmp_path, recorder, cap=0.0004)
    list(provider.stream(request(model="gpt-4o")))  # 100 in + 20 out at gpt-4o rates = $0.00045
    assert provider._spend.spent("openai_key") == pytest.approx(0.00045)
    with pytest.raises(SpendCapReached):
        list(provider.stream(request(model="gpt-4o")))
    assert len(recorder.requests) == 1


def test_key_never_in_logs_or_url(tmp_path, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    recorder = Recorder(body=fixture("optin_openai_text.sse"))
    list(_provider(tmp_path, recorder).stream(request()))
    models_recorder = Recorder(json_body={"data": [{"id": "gpt-4o"}]})
    models = OpenAIKeyProvider(secret_store=FakeSecrets(openai_api_key=SECRET), transport=models_recorder.transport)
    assert [m.id for m in models.list_models()] == ["gpt-4o"]
    assert SECRET not in caplog.text
    for req in recorder.requests + models_recorder.requests:
        assert SECRET not in str(req.url)
        assert SECRET.encode() not in req.content


def test_sensitive_text_is_redacted_before_egress(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_openai_text.sse"))
    list(_provider(tmp_path, recorder).stream(request(text="mail me at a.b@example.com")))
    assert b"a.b@example.com" not in recorder.requests[0].content


def test_spend_cap_error_is_a_provider_error() -> None:
    assert issubclass(SpendCapReached, ProviderError)
