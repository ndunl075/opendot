import httpx
import pytest
from optin_support import FakeSecrets, Recorder, fixture, request

from opendot_core.providers import (
    Completed,
    IncompleteResponse,
    LocalProvider,
    Provider,
    ProviderUnavailable,
    TextDelta,
    ToolCall,
)


def _provider(recorder, **kwargs):
    return LocalProvider(secret_store=FakeSecrets(), transport=recorder.transport, **kwargs)


def test_free_loopback_default_and_no_key_needed() -> None:
    recorder = Recorder(body=fixture("optin_openai_text.sse"))
    provider = _provider(recorder)
    assert isinstance(provider, Provider) and provider.name == "local" and provider.paid is False
    assert provider.base_url == "http://127.0.0.1:11434"
    events = list(provider.stream(request("llama3.2")))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
    assert isinstance(events[-1], Completed)
    sent = recorder.requests[0]
    assert sent.url.host == "127.0.0.1" and sent.url.port == 11434 and sent.url.path == "/v1/chat/completions"
    assert "authorization" not in sent.headers


def test_no_redaction_for_local() -> None:
    recorder = Recorder(body=fixture("optin_openai_text.sse"))
    list(_provider(recorder).stream(request("m", text="a.b@example.com")))
    assert b"a.b@example.com" in recorder.requests[0].content


@pytest.mark.parametrize("url", ["http://127.0.0.1:1234", "http://localhost:11434", "http://[::1]:11434"])
def test_loopback_urls_allowed(url) -> None:
    assert LocalProvider(secret_store=FakeSecrets(), base_url=url).base_url == url


@pytest.mark.parametrize("url", ["http://192.168.1.5:11434", "https://api.example.com", "http://0.0.0.0:11434"])
def test_non_loopback_refused_by_default(url) -> None:
    with pytest.raises(ValueError):
        LocalProvider(secret_store=FakeSecrets(), base_url=url)


def test_remote_needs_explicit_opt_in() -> None:
    provider = LocalProvider(secret_store=FakeSecrets(), base_url="http://192.168.1.5:11434", allow_remote=True)
    assert provider.base_url == "http://192.168.1.5:11434"


def test_optional_key_is_sent_when_present() -> None:
    recorder = Recorder(body=fixture("optin_openai_text.sse"))
    provider = LocalProvider(secret_store=FakeSecrets(local_api_key="lk-1"), transport=recorder.transport)
    list(provider.stream(request("m")))
    assert recorder.requests[0].headers["authorization"] == "Bearer lk-1"


def test_tool_call() -> None:
    recorder = Recorder(body=fixture("optin_openai_tool.sse"))
    events = list(_provider(recorder).stream(request("m")))
    assert any(isinstance(e, ToolCall) for e in events)


def test_incomplete() -> None:
    recorder = Recorder(body=fixture("optin_openai_incomplete.sse"))
    with pytest.raises(IncompleteResponse):
        list(_provider(recorder).stream(request("m")))


def test_server_error_maps() -> None:
    recorder = Recorder(status=500, json_body={"error": {"message": "boom"}})
    with pytest.raises(ProviderUnavailable):
        list(_provider(recorder).stream(request("m")))


def test_connection_refused_is_unavailable() -> None:
    def refuse(_request):
        raise httpx.ConnectError("refused")

    provider = LocalProvider(secret_store=FakeSecrets(), transport=httpx.MockTransport(refuse))
    with pytest.raises(ProviderUnavailable):
        list(provider.stream(request("m")))


def test_list_models() -> None:
    recorder = Recorder(json_body={"data": [{"id": "llama3.2"}]})
    assert [m.id for m in _provider(recorder).list_models()] == ["llama3.2"]
    assert recorder.requests[0].url.path == "/v1/models"
