"""A scripted, deterministic model provider. No network, no account.

A script is a list of turns. Each call to :meth:`ScriptedProvider.stream` plays
the next turn: text, tool calls, a raised ``ProviderError`` (for example
``UsageLimitExceeded``), or a simulated process crash. Every request received
is recorded so scenarios can compare prompt prefixes across steps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

from ..providers.types import ChatRequest, Completed, ModelInfo, StreamEvent, TextDelta, ToolCall, Usage

DEFAULT_CATALOG: tuple[ModelInfo, ...] = (
    ModelInfo(id="fake-luna", display_name="Fake Luna", family="luna", supported_efforts=["low", "medium", "high"]),
    ModelInfo(id="fake-terra", display_name="Fake Terra", family="terra", supported_efforts=["low", "medium", "high"]),
    ModelInfo(id="fake-sol", display_name="Fake Sol", family="sol", supported_efforts=["low", "medium", "high"]),
)


class ScriptExhausted(AssertionError):
    """The code under test made more provider calls than the script allows."""


class SimulatedCrash(BaseException):
    """The process 'died' mid-request. A BaseException so no ``except Exception`` can swallow it."""


@dataclass
class Turn:
    text: str = ""
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    error: Exception | None = None
    crash: bool = False
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=100, output_tokens=20))


def _usage(usage: Usage | None, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> Usage:
    if usage is not None:
        return usage
    return Usage(input_tokens=input_tokens, cached_input_tokens=cached_input_tokens, output_tokens=output_tokens)


def text_turn(
    text: str,
    *,
    usage: Usage | None = None,
    input_tokens: int = 100,
    cached_input_tokens: int = 0,
    output_tokens: int = 20,
) -> Turn:
    return Turn(text=text, usage=_usage(usage, input_tokens, cached_input_tokens, output_tokens))


def tool_turn(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    usage: Usage | None = None,
    input_tokens: int = 100,
    cached_input_tokens: int = 0,
    output_tokens: int = 20,
    text: str = "",
) -> Turn:
    return Turn(
        text=text,
        tool_calls=[(name, arguments or {})],
        usage=_usage(usage, input_tokens, cached_input_tokens, output_tokens),
    )


def error_turn(error: Exception) -> Turn:
    return Turn(error=error)


def crash_turn() -> Turn:
    return Turn(crash=True)


class ScriptedProvider:
    """Implements the ``Provider`` protocol from a script of turns."""

    def __init__(
        self,
        script: Sequence[Turn] = (),
        *,
        name: str = "chatgpt_plan",
        paid: bool = False,
        catalog: Sequence[ModelInfo] | None = None,
    ) -> None:
        self.name = name
        self.paid = paid
        self.catalog: list[ModelInfo] = list(catalog if catalog is not None else DEFAULT_CATALOG)
        self._script: list[Turn] = list(script)
        self._position = 0
        self._call_counter = 0
        self.calls = 0
        """Number of ``stream`` calls received, including ones that raised."""
        self.requests: list[ChatRequest] = []
        """Deep copies of every request received, in order."""

    def extend(self, turns: Sequence[Turn]) -> None:
        self._script.extend(turns)

    @property
    def remaining(self) -> int:
        return len(self._script) - self._position

    def list_models(self) -> list[ModelInfo]:
        return [model.model_copy(deep=True) for model in self.catalog]

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        self.calls += 1
        self.requests.append(request.model_copy(deep=True))
        if self._position >= len(self._script):
            raise ScriptExhausted(f"{self.name}: call {self.calls} has no scripted turn")
        turn = self._script[self._position]
        self._position += 1
        return self._play(turn, request)

    def _play(self, turn: Turn, request: ChatRequest) -> Iterator[StreamEvent]:
        if turn.crash:
            raise SimulatedCrash(f"{self.name} crashed mid-request")
        if turn.error is not None:
            raise turn.error
        if turn.text:
            yield TextDelta(text=turn.text)
        calls: list[ToolCall] = []
        for tool_name, arguments in turn.tool_calls:
            self._call_counter += 1
            call = ToolCall(
                call_id=f"call_{self._call_counter}",
                name=tool_name,
                arguments=json.dumps(arguments, sort_keys=True, separators=(",", ":")),
            )
            calls.append(call)
            yield call
        yield Completed(text=turn.text, model=request.model, usage=turn.usage, tool_calls=calls)
