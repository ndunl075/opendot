import json

import pytest
from optin_support import SECRET, FakeSecrets, Recorder, fixture, make_spend, request

from opendot_core.providers import (
    AnthropicKeyProvider,
    AuthRequired,
    Completed,
    IncompleteResponse,
    InputItem,
    Provider,
    ProviderUnavailable,
    RateLimited,
    SpendCapReached,
    TextDelta,
    ToolCall,
    UnsupportedCapability,
)

MODEL = "claude-sonnet-5"


def _provider(tmp_path, recorder, cap=5.0, secrets=None):
    return AnthropicKeyProvider(
        secret_store=secrets or FakeSecrets(anthropic_api_key=SECRET),
        transport=recorder.transport,
        spend=make_spend(tmp_path, cap),
    )


def test_identity() -> None:
    provider = AnthropicKeyProvider(secret_store=FakeSecrets())
    assert isinstance(provider, Provider)
    assert provider.name == "anthropic_key" and provider.paid is True


def test_happy_stream_uses_x_api_key(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_anthropic_text.sse"))
    events = list(_provider(tmp_path, recorder).stream(request(MODEL, instructions="be brief")))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hi ", "there"]
    done = events[-1]
    assert isinstance(done, Completed) and done.text == "Hi there"
    assert (done.usage.input_tokens, done.usage.output_tokens) == (30, 7)
    sent = recorder.requests[0]
    assert sent.url.host == "api.anthropic.com" and sent.url.path == "/v1/messages"
    assert sent.headers["x-api-key"] == SECRET
    assert "authorization" not in sent.headers
    assert SECRET not in str(sent.url)
    body = json.loads(sent.content)
    assert body["stream"] is True and body["system"] == "be brief" and body["max_tokens"] > 0


def test_tool_call(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_anthropic_tool.sse"))
    events = list(_provider(tmp_path, recorder).stream(request(MODEL)))
    call = next(e for e in events if isinstance(e, ToolCall))
    assert (call.call_id, call.name, call.arguments) == ("toolu_1", "get_weather", '{"city":"Oslo"}')
    assert isinstance(events[-1], Completed) and events[-1].tool_calls == [call]


def test_tool_history_is_sent_as_tool_blocks(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_anthropic_text.sse"))
    req = request(MODEL)
    req.input += [
        InputItem(role="assistant", tool_call_id="toolu_1", tool_name="get_weather", tool_arguments='{"city":"Oslo"}'),
        InputItem(role="tool", tool_call_id="toolu_1", content="rain"),
    ]
    list(_provider(tmp_path, recorder).stream(req))
    messages = json.loads(recorder.requests[0].content)["messages"]
    assert messages[1]["content"][0]["type"] == "tool_use" and messages[1]["content"][0]["input"] == {"city": "Oslo"}
    assert messages[2]["content"][0]["type"] == "tool_result"


def test_incomplete_stream(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_anthropic_incomplete.sse"))
    with pytest.raises(IncompleteResponse):
        list(_provider(tmp_path, recorder).stream(request(MODEL)))


def test_in_stream_overload_error(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_anthropic_error.sse"))
    with pytest.raises(ProviderUnavailable):
        list(_provider(tmp_path, recorder).stream(request(MODEL)))


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, AuthRequired),
        (403, AuthRequired),
        (429, RateLimited),
        (500, ProviderUnavailable),
        (529, ProviderUnavailable),
    ],
)
def test_http_errors_map(tmp_path, status, error) -> None:
    recorder = Recorder(status=status, json_body={"type": "error", "error": {"message": f"x {SECRET}"}})
    with pytest.raises(error) as caught:
        list(_provider(tmp_path, recorder).stream(request(MODEL)))
    assert caught.value.status == status
    assert SECRET not in str(caught.value) and SECRET not in caught.value.detail
    assert len(recorder.requests) == 1


def test_cap_zero_fails_closed_before_any_request(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_anthropic_text.sse"))
    with pytest.raises(SpendCapReached):
        list(_provider(tmp_path, recorder, cap=None).stream(request(MODEL)))
    assert recorder.requests == []


def test_spend_recorded(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_anthropic_text.sse"))
    provider = _provider(tmp_path, recorder)
    list(provider.stream(request(MODEL)))
    assert provider._spend.spent("anthropic_key") == pytest.approx((30 * 3 + 7 * 15) / 1_000_000)


def test_incomplete_stream_still_counts_the_input(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_anthropic_incomplete.sse"))
    provider = _provider(tmp_path, recorder)
    with pytest.raises(IncompleteResponse):
        list(provider.stream(request(MODEL)))
    assert provider._spend.spent("anthropic_key") > 0


def test_missing_key_makes_no_request(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_anthropic_text.sse"))
    with pytest.raises(AuthRequired):
        list(_provider(tmp_path, recorder, secrets=FakeSecrets()).stream(request(MODEL)))
    assert recorder.requests == []


def test_structured_output_unsupported_without_network(tmp_path) -> None:
    recorder = Recorder(body=fixture("optin_anthropic_text.sse"))
    with pytest.raises(UnsupportedCapability):
        list(_provider(tmp_path, recorder).stream(request(MODEL, structured_output={"type": "object"})))
    assert recorder.requests == []


def test_list_models_and_key_not_in_url() -> None:
    recorder = Recorder(json_body={"data": [{"id": MODEL, "display_name": "Sonnet"}]})
    provider = AnthropicKeyProvider(secret_store=FakeSecrets(anthropic_api_key=SECRET), transport=recorder.transport)
    models = provider.list_models()
    assert models[0].id == MODEL and models[0].display_name == "Sonnet"
    assert SECRET not in str(recorder.requests[0].url)
