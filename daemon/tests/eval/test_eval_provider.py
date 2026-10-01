from __future__ import annotations

import pytest

from opendot_core.eval.fake_provider import (
    ScriptedProvider,
    ScriptExhausted,
    SimulatedCrash,
    crash_turn,
    error_turn,
    text_turn,
    tool_turn,
)
from opendot_core.providers.base import Provider
from opendot_core.providers.errors import UsageLimitExceeded
from opendot_core.providers.types import ChatRequest, Completed, InputItem, TextDelta, ToolCall, Usage


def _request(model: str = "fake-luna", text: str = "hi") -> ChatRequest:
    return ChatRequest(model=model, instructions="rules", input=[InputItem(role="user", content=text)])


def test_implements_the_provider_protocol() -> None:
    provider = ScriptedProvider()
    assert isinstance(provider, Provider)
    assert provider.name == "chatgpt_plan"
    assert provider.paid is False
    assert {m.family for m in provider.list_models()} == {"luna", "terra", "sol"}


def test_text_turn_streams_delta_then_one_completed_with_usage() -> None:
    provider = ScriptedProvider([text_turn("hello", input_tokens=10, cached_input_tokens=4, output_tokens=3)])
    events = list(provider.stream(_request("fake-terra")))
    assert isinstance(events[0], TextDelta) and events[0].text == "hello"
    done = events[-1]
    assert isinstance(done, Completed)
    assert done.model == "fake-terra"
    assert done.usage == Usage(input_tokens=10, cached_input_tokens=4, output_tokens=3)
    assert sum(isinstance(e, Completed) for e in events) == 1


def test_tool_turn_emits_tool_call_with_stable_ids() -> None:
    provider = ScriptedProvider([tool_turn("reminder_set", {"text": "x", "minutes": 1}), tool_turn("memory_search")])
    first = list(provider.stream(_request()))
    second = list(provider.stream(_request()))
    call = next(e for e in first if isinstance(e, ToolCall))
    assert call.name == "reminder_set"
    assert call.arguments == '{"minutes":1,"text":"x"}'
    assert [c.call_id for c in (next(e for e in second if isinstance(e, ToolCall)),)] != [call.call_id]
    assert isinstance(first[-1], Completed) and first[-1].tool_calls == [call]


def test_error_turn_raises_the_provider_error_and_counts_the_call() -> None:
    provider = ScriptedProvider([error_turn(UsageLimitExceeded("429"))])
    with pytest.raises(UsageLimitExceeded):
        list(provider.stream(_request()))
    assert provider.calls == 1


def test_crash_turn_is_not_an_exception() -> None:
    provider = ScriptedProvider([crash_turn()])
    with pytest.raises(SimulatedCrash):
        list(provider.stream(_request()))
    assert not issubclass(SimulatedCrash, Exception)


def test_records_every_request_as_a_copy_and_counts_calls() -> None:
    provider = ScriptedProvider([text_turn("a"), text_turn("b")])
    request = _request(text="first")
    list(provider.stream(request))
    request.input.append(InputItem(role="user", content="mutated afterwards"))
    list(provider.stream(_request(text="second")))
    assert provider.calls == 2
    assert [len(r.input) for r in provider.requests] == [1, 1]
    assert provider.requests[1].input[0].content == "second"


def test_running_past_the_script_fails_loudly() -> None:
    provider = ScriptedProvider([text_turn("only")])
    list(provider.stream(_request()))
    with pytest.raises(ScriptExhausted):
        provider.stream(_request())
    assert provider.calls == 2


def test_opt_in_provider_can_be_named_and_paid() -> None:
    provider = ScriptedProvider(name="openai_key", paid=True)
    assert (provider.name, provider.paid) == ("openai_key", True)
