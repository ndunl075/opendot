"""Tests for the chatgpt_plan provider against fake OAuth and Responses servers (no real network).

The SSE and error fixtures under ``fixtures/chatgpt_plan`` are SYNTHETIC, written from OpenAI's docs, until the
user's first real sign-in replaces them. Section numbers below refer to ARCHITECTURE.md 6.1.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs, urlparse

import pytest

from opendot_core.providers import (
    AuthRequired,
    ChatRequest,
    Completed,
    IncompleteResponse,
    InputItem,
    PlanNotGranted,
    Provider,
    ProviderUnavailable,
    RateLimited,
    TextDelta,
    ToolCall,
    ToolSpec,
    UnsupportedCapability,
    Usage,
    UsageLimitExceeded,
    UserNotEligible,
)
from opendot_core.providers.chatgpt_plan import (
    FORBIDDEN_PARAMETERS,
    PLAN_SCOPE,
    SCOPES,
    ChatGPTPlanProvider,
    credit_spend_detected,
    parse_family,
)
from tests.providers.chatgpt_plan_fakes import CLIENT_ID, FakeOpenAI, MemoryTokenStore, complete_browser_leg


@pytest.fixture
def fake() -> FakeOpenAI:
    return FakeOpenAI()


@pytest.fixture
def store() -> MemoryTokenStore:
    return MemoryTokenStore()


@pytest.fixture
def provider(fake, store, tmp_path) -> ChatGPTPlanProvider:
    p = ChatGPTPlanProvider(store, tmp_path / "state.json", http=fake.client(), clock=lambda: fake.now)
    yield p
    p.cancel_sign_in()


def sign_in(provider: ChatGPTPlanProvider, fake: FakeOpenAI, **kw):
    start = provider.begin_sign_in()
    fake.learn_authorize(start.authorize_url)
    complete_browser_leg(start, **kw)
    return provider.complete_sign_in(timeout=5)


def req(**kw) -> ChatRequest:
    return ChatRequest(model="gpt-6-luna", instructions="Be brief.", input=[InputItem(role="user", content="hi")], **kw)


def run(provider, request=None) -> list:
    return list(provider.stream(request or req()))


# ---- protocol -----------------------------------------------------------------------------------


def test_implements_provider_protocol(provider):
    assert isinstance(provider, Provider)
    assert provider.name == "chatgpt_plan"
    assert provider.paid is False


# ---- 1. host id ---------------------------------------------------------------------------------


def test_host_id_is_opaque_stable_and_persisted(fake, store, tmp_path):
    a = ChatGPTPlanProvider(store, tmp_path / "s.json", http=fake.client())
    first = a.host_id
    assert first.startswith("urn:uuid:")
    assert a.host_id == first
    b = ChatGPTPlanProvider(store, tmp_path / "s.json", http=fake.client())
    assert b.host_id == first
    c = ChatGPTPlanProvider(store, tmp_path / "other.json", http=fake.client())
    assert c.host_id != first


def test_host_id_not_derived_from_account(provider, fake):
    before = provider.host_id
    sign_in(provider, fake)
    assert provider.host_id == before
    assert "sam" not in before and "sub_1" not in before


# ---- 2. sign-in ---------------------------------------------------------------------------------


def test_authorize_url_uses_pkce_loopback_and_host_id(provider, fake):
    start = provider.begin_sign_in()
    q = {k: v[0] for k, v in parse_qs(urlparse(start.authorize_url).query).items()}
    assert urlparse(start.authorize_url).path == "/oauth/authorize"
    assert q["code_challenge_method"] == "S256"
    assert q["response_type"] == "code"
    assert q["scope"] == SCOPES and PLAN_SCOPE in q["scope"].split()
    assert q["ext_agent_host_id"] == provider.host_id
    assert q["redirect_uri"] == start.redirect_uri
    assert start.redirect_uri.startswith("http://127.0.0.1:") and start.redirect_uri.endswith("/auth/callback")
    assert "client_secret" not in q and "verifier" not in start.authorize_url
    assert q["state"] and q["nonce"]


def test_full_sign_in_stores_tokens_only_in_store(provider, fake, store):
    status = sign_in(provider, fake)
    assert status.connected and status.plan_granted and not status.paused
    assert status.email == "sam@example.test"
    blob = store.values["chatgpt_plan.credentials"]
    assert "at-1" in blob and "rt-1" in blob
    # 4. nothing token-like in anything UI-facing
    assert "at-1" not in status.model_dump_json() and "rt-1" not in status.model_dump_json()
    assert "urn:uuid" not in status.model_dump_json()
    assert provider._state.get("client_id") == CLIENT_ID
    assert "at-1" not in provider._state.path.read_text() and "rt-1" not in provider._state.path.read_text()


def test_callback_with_wrong_state_is_rejected_and_does_not_consume_sign_in(provider, fake):
    start = provider.begin_sign_in()
    fake.learn_authorize(start.authorize_url)
    complete_browser_leg(start, state="wrong")
    assert not provider._pending.done.is_set()
    complete_browser_leg(start)
    assert provider.complete_sign_in(timeout=5).connected


def test_callback_error_becomes_auth_required(provider, fake):
    start = provider.begin_sign_in()
    complete_browser_leg(start, extra="&error=access_denied")
    with pytest.raises(AuthRequired):
        provider.complete_sign_in(timeout=5)


def test_callback_without_registered_client_id_fails(provider, fake):
    start = provider.begin_sign_in()
    complete_browser_leg(start, client_id="dynamic_agent_client")
    with pytest.raises(AuthRequired):
        provider.complete_sign_in(timeout=5)


def test_complete_without_begin_fails(provider):
    with pytest.raises(AuthRequired):
        provider.complete_sign_in(timeout=0.1)


def test_id_token_with_wrong_nonce_rejected(provider, fake, store):
    start = provider.begin_sign_in()
    fake.learn_authorize(start.authorize_url)
    fake.nonce = "some-other-nonce"
    complete_browser_leg(start)
    with pytest.raises(AuthRequired):
        provider.complete_sign_in(timeout=5)
    assert "chatgpt_plan.credentials" not in store.values


def test_second_sign_in_reuses_saved_client_id(provider, fake):
    sign_in(provider, fake)
    start = provider.begin_sign_in()
    q = parse_qs(urlparse(start.authorize_url).query)
    assert q["client_id"] == [CLIENT_ID]
    assert "agent_name_hint" not in q


# ---- 3. plan check ------------------------------------------------------------------------------


def test_missing_plan_scope_raises_plan_not_granted_and_revokes(provider, fake, store):
    fake.granted_scope = "openid profile email offline_access"
    with pytest.raises(PlanNotGranted) as info:
        sign_in(provider, fake)
    assert "Plus or Pro" in str(info.value)
    assert "chatgpt_plan.credentials" not in store.values
    assert fake.revoked == ["rt-1"]


def test_ineligible_account_at_token_endpoint(provider, fake):
    fake.token_error = (403, {"error": {"code": "subscription_sharing_user_not_eligible"}})
    with pytest.raises(UserNotEligible) as info:
        sign_in(provider, fake)
    assert "Plus or Pro" in str(info.value) or "subscription_sharing" in str(info.value)


# ---- 4. tokens ----------------------------------------------------------------------------------


def test_tokens_never_logged(provider, fake, caplog):
    caplog.set_level(logging.DEBUG)
    sign_in(provider, fake)
    fake.queue_sse("text_only.sse")
    run(provider)
    assert "at-1" not in caplog.text and "rt-1" not in caplog.text


def test_token_repr_hides_secrets(provider, fake):
    sign_in(provider, fake)
    assert "at-1" not in repr(provider._load())


def test_disconnect_revokes_and_clears(provider, fake, store):
    sign_in(provider, fake)
    status = provider.disconnect()
    assert fake.revoked == ["rt-1"]
    assert not status.connected and status.models_loaded == 0 and store.values == {}
    with pytest.raises(AuthRequired):
        run(provider)


def test_disconnect_when_revoke_fails_still_clears_locally(provider, fake, store):
    sign_in(provider, fake)
    fake.revoke_fails = True
    status = provider.disconnect()
    assert not status.connected and store.values == {}
    assert "could not be confirmed" in status.message


def test_refresh_rotates_token_before_expiry(provider, fake, store):
    sign_in(provider, fake)
    fake.now += 3600 - 30  # inside the 60 second refresh window
    fake.queue_sse("text_only.sse")
    run(provider)
    assert fake.refresh_count == 1
    assert "rt-2" in store.values["chatgpt_plan.credentials"]


def test_401_refreshes_once_then_succeeds(provider, fake):
    sign_in(provider, fake)
    fake.valid_access = "at-rotated"  # server revoked our access token
    fake.queue_sse("text_only.sse")
    events = run(provider)
    assert fake.refresh_count == 1
    assert isinstance(events[-1], Completed)


def test_401_twice_raises_auth_required(provider, fake):
    sign_in(provider, fake)
    fake.valid_access = "never"
    fake.refresh_yields_bad_token = True
    with pytest.raises(AuthRequired):
        run(provider)
    assert fake.refresh_count == 1  # exactly one refresh attempt


def test_dead_refresh_token_raises_auth_required_and_clears(provider, fake, store):
    sign_in(provider, fake)
    fake.now += 7200
    fake.refresh_error = (400, {"error": "invalid_grant"})
    with pytest.raises(AuthRequired):
        run(provider)
    assert store.values == {}


# ---- 5. models ----------------------------------------------------------------------------------


def test_models_fetched_after_sign_in_and_families_parsed(provider, fake):
    status = sign_in(provider, fake)
    assert status.models_loaded == 5  # the hidden one is skipped
    by_id = {m.id: m for m in provider.models}
    assert by_id["gpt-6-luna"].family == "luna" and by_id["gpt-6-luna"].supported_efforts == ["low", "high"]
    assert by_id["gpt-5.6-terra"].family == "terra"
    assert by_id["gpt-6-sol"].family == "sol"
    assert by_id["gpt-6-astra"].family == "astra"
    assert by_id["gpt-5.5"].family is None
    assert "hidden-model" not in by_id


def test_parse_family_words_only():
    assert parse_family("gpt-7-SOL-mini") == "sol"
    assert parse_family("console") is None  # 'sol' inside a word does not count


def test_list_models_bad_catalog_is_unavailable(provider, fake):
    sign_in(provider, fake)
    fake.models_response = (200, b'{"nope":1}')
    with pytest.raises(ProviderUnavailable):
        provider.list_models()


# ---- 6. requests and streaming ------------------------------------------------------------------


def test_text_stream_events_and_completed_usage(provider, fake):
    sign_in(provider, fake)
    fake.queue_sse("text_only.sse")
    events = run(provider)
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hello", ", world"]
    done = events[-1]
    assert isinstance(done, Completed) and done.text == "Hello, world" and done.model == "gpt-6-luna"
    assert (done.usage.input_tokens, done.usage.output_tokens) == (12, 4)
    assert done.usage.raw["total_tokens"] == 16
    assert sum(isinstance(e, Completed) for e in events) == 1


def test_tool_call_stream(provider, fake):
    sign_in(provider, fake)
    fake.queue_sse("tool_call.sse")
    events = run(provider, req(tools=[ToolSpec(name="get_weather", parameters={"type": "object"})]))
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert len(calls) == 1 and calls[0].name == "get_weather" and calls[0].call_id == "call_abc"
    assert json.loads(calls[0].arguments) == {"city": "Columbus"}
    done = events[-1]
    assert isinstance(done, Completed) and done.tool_calls == calls and done.usage.reasoning_tokens == 2


def test_cached_tokens_usage(provider, fake):
    sign_in(provider, fake)
    fake.queue_sse("cached_usage.sse")
    done = run(provider)[-1]
    assert done.usage.cached_input_tokens == 1792 and done.usage.input_tokens == 2000
    assert done.usage.raw["input_tokens_details"]["cached_tokens"] == 1792


def test_truncated_stream_is_incomplete_not_success(provider, fake):
    sign_in(provider, fake)
    fake.queue_sse("truncated.sse")
    seen = []
    with pytest.raises(IncompleteResponse):
        for event in provider.stream(req()):
            seen.append(event)
    assert seen and not any(isinstance(e, Completed) for e in seen)


def test_outgoing_body_full_history_store_false_no_previous_response_id(provider, fake):
    sign_in(provider, fake)
    fake.queue_sse("text_only.sse")
    history = [
        InputItem(role="user", content="first"),
        InputItem(role="assistant", content="answer"),
        InputItem(role="assistant", tool_call_id="c1", tool_name="lookup", tool_arguments='{"q":1}'),
        InputItem(role="tool", tool_call_id="c1", content="result"),
        InputItem(role="user", content="second"),
    ]
    run(provider, ChatRequest(model="m", instructions="sys", input=history, effort="low"))
    body = fake.last_body()
    assert body["store"] is False and body["stream"] is True
    assert "previous_response_id" not in body
    assert body["instructions"] == "sys" and body["reasoning"] == {"effort": "low"}
    assert body["input"][0] == {"role": "user", "content": "first"}
    assert body["input"][2] == {"type": "function_call", "call_id": "c1", "name": "lookup", "arguments": '{"q":1}'}
    assert body["input"][3] == {"type": "function_call_output", "call_id": "c1", "output": "result"}
    assert len(body["input"]) == 5


def test_system_role_is_sent_as_developer(provider, fake):
    sign_in(provider, fake)
    fake.queue_sse("text_only.sse")
    run(provider, ChatRequest(model="m", input=[InputItem(role="system", content="rules")]))
    assert fake.last_body()["input"][0]["role"] == "developer"


# ---- 7/8. forbidden parameters and tools --------------------------------------------------------


def test_outgoing_body_has_no_forbidden_parameters_and_only_function_tools(provider, fake):
    sign_in(provider, fake)
    fake.queue_sse("text_only.sse")
    request = req(
        tools=[ToolSpec(name="zeta"), ToolSpec(name="alpha", description="d")],
        effort="high",
        structured_output={"type": "object", "properties": {}},
    )
    run(provider, request)
    body = fake.last_body()
    assert not (FORBIDDEN_PARAMETERS & set(body)), FORBIDDEN_PARAMETERS & set(body)
    assert FORBIDDEN_PARAMETERS >= {
        "background", "conversation", "max_output_tokens", "max_tool_calls", "metadata", "moderation",
        "multi_agent", "prompt", "prompt_cache_retention", "safety_identifier", "temperature", "top_logprobs",
        "top_p", "truncation", "user",
    }  # fmt: skip
    assert [t["type"] for t in body["tools"]] == ["function", "function"]
    assert [t["name"] for t in body["tools"]] == ["alpha", "zeta"]  # sorted: stable prefix for caching
    for forbidden_tool in ("web_search", "file_search", "code_interpreter", "image_generation", "mcp", "computer_use"):
        assert forbidden_tool not in json.dumps(body["tools"])


def test_headers_carry_bearer_and_no_spoofed_client_headers(provider, fake):
    sign_in(provider, fake)
    fake.queue_sse("text_only.sse")
    run(provider)
    headers = fake.api_requests[-1].headers
    assert headers["authorization"] == "Bearer at-1"
    assert "originator" not in headers and "openai-organization" not in headers


# ---- 9. errors ----------------------------------------------------------------------------------


def test_usage_limit_pauses_then_refuses_until_resume(provider, fake):
    sign_in(provider, fake)
    fake.queue_error(429, "err_429_usage_limit.json")
    with pytest.raises(UsageLimitExceeded):
        run(provider)
    assert provider.paused and provider.status().paused
    before = len(fake.api_requests)
    with pytest.raises(UsageLimitExceeded):
        run(provider)
    assert len(fake.api_requests) == before  # refused locally, nothing sent
    provider.resume()
    assert not provider.paused
    fake.queue_sse("text_only.sse")
    assert isinstance(run(provider)[-1], Completed)


def test_pause_persists_across_instances(fake, store, tmp_path):
    a = ChatGPTPlanProvider(store, tmp_path / "s.json", http=fake.client(), clock=lambda: fake.now)
    sign_in(a, fake)
    fake.queue_error(429, "err_429_usage_limit.json")
    with pytest.raises(UsageLimitExceeded):
        run(a)
    b = ChatGPTPlanProvider(store, tmp_path / "s.json", http=fake.client(), clock=lambda: fake.now)
    assert b.paused
    with pytest.raises(UsageLimitExceeded):
        run(b)
    a.cancel_sign_in()


def test_usage_limit_inside_stream_also_pauses(provider, fake):
    sign_in(provider, fake)
    fake.queue_sse("failed_usage_limit.sse")
    with pytest.raises(UsageLimitExceeded):
        run(provider)
    assert provider.paused


def test_ordinary_rate_limit_does_not_pause(provider, fake):
    sign_in(provider, fake)
    fake.queue_error(429, "err_429_rate_limit.json")
    with pytest.raises(RateLimited):
        run(provider)
    assert not provider.paused


def test_403_not_eligible(provider, fake):
    sign_in(provider, fake)
    fake.queue_error(403, "err_403_not_eligible.json")
    with pytest.raises(UserNotEligible) as info:
        run(provider)
    assert info.value.status == 403 and not provider.paused


def test_400_unsupported_capability(provider, fake):
    sign_in(provider, fake)
    fake.queue_error(400, "err_400_unsupported.json")
    with pytest.raises(UnsupportedCapability):
        run(provider)


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_is_provider_unavailable(provider, fake, status):
    sign_in(provider, fake)
    fake.api_queue.append((status, b"oops", {"content-type": "text/plain"}))
    with pytest.raises(ProviderUnavailable):
        run(provider)


def test_network_failure_is_provider_unavailable(fake, store, tmp_path):
    import httpx

    state = {"down": False}

    def handler(request):
        if state["down"]:
            raise httpx.ConnectError("boom")
        return fake.handle(request)

    p = ChatGPTPlanProvider(
        store, tmp_path / "s.json", http=httpx.Client(transport=httpx.MockTransport(handler)), clock=lambda: fake.now
    )
    sign_in(p, fake)
    state["down"] = True
    with pytest.raises(ProviderUnavailable):
        run(p)
    with pytest.raises(ProviderUnavailable):
        p.list_models()
    p.cancel_sign_in()


def test_no_fallback_to_another_provider_on_error(provider, fake):
    from opendot_core.providers import ProviderRegistry, ProviderSwitchForbidden

    sign_in(provider, fake)
    registry = ProviderRegistry()
    registry.register(provider)
    fake.queue_error(403, "err_403_not_eligible.json")
    with pytest.raises(UserNotEligible):
        list(registry.stream("chatgpt_plan", req()))
    with pytest.raises(ProviderSwitchForbidden):
        registry.fall_back("chatgpt_plan", to="openai_key")


# ---- 10. credits --------------------------------------------------------------------------------


def test_usage_raw_exposed_and_credit_spend_flagged(provider, fake):
    sign_in(provider, fake)
    fake.queue_sse("credit_usage.sse")
    done = run(provider)[-1]
    assert done.usage.raw["credits_used"] == 0.4
    flagged = provider.credit_spend_detected(done.usage)
    assert flagged and "credits_used" in flagged[0]


def test_plain_usage_is_not_flagged(provider, fake):
    sign_in(provider, fake)
    fake.queue_sse("cached_usage.sse")
    assert credit_spend_detected(run(provider)[-1].usage) == []


@pytest.mark.parametrize(
    "raw",
    [
        {"billing": {"amount_cents": 5}},
        {"x": {"estimated_cost_usd": 0.01}},
        {"overage_charge": "0.02"},
        {"usage": [{"balance_used": 3}]},
        {"paid_with_credits": True},
        {"weird_billing_thing": "yes"},
    ],
)
def test_credit_helper_flags_unknown_billing_keys(raw):
    assert credit_spend_detected(Usage(raw=raw))


@pytest.mark.parametrize(
    "raw",
    [{}, {"input_tokens": 5, "output_tokens": 1}, {"credits_used": 0}, {"billing": {"cost": 0.0}}, {"cost": "0"}],
)
def test_credit_helper_ignores_zero_and_token_fields(raw):
    assert credit_spend_detected(Usage(raw=raw)) == []



def test_an_iterator_created_before_a_usage_limit_does_not_send_after_the_pause(provider, fake):
    """Review F9: the pause is checked when the request is sent, not only when the iterator is made."""
    sign_in(provider, fake)
    request = ChatRequest(model="m", input=[InputItem(role="user", content="hi")])
    early = provider.stream(request)  # created, not yet consumed
    fake.queue_error(429, "err_429_usage_limit.json")
    with pytest.raises(UsageLimitExceeded):
        run(provider)
    before = len(fake.api_requests)

    with pytest.raises(UsageLimitExceeded):
        list(early)

    assert len(fake.api_requests) == before


def test_startup_discovery_loads_the_catalog_for_an_existing_session(fake, store, tmp_path):
    first = ChatGPTPlanProvider(store, tmp_path / "s.json", http=fake.client(), clock=lambda: fake.now)
    sign_in(first, fake)
    restarted = ChatGPTPlanProvider(store, tmp_path / "s.json", http=fake.client(), clock=lambda: fake.now)
    assert restarted.models == []

    assert restarted.load_models_on_start()
    assert restarted.models


def test_startup_discovery_is_quiet_when_nobody_is_signed_in(fake, store, tmp_path):
    fresh = ChatGPTPlanProvider(store, tmp_path / "s.json", http=fake.client(), clock=lambda: fake.now)

    assert fresh.load_models_on_start() == []
