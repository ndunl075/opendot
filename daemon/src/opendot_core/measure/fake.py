"""A deterministic fake provider for `opendot measure --dry-run`. No account, no network."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterator

from ..providers.errors import UnsupportedCapability
from ..providers.types import ChatRequest, Completed, ModelInfo, StreamEvent, TextDelta, Usage


def _tokens(text: str) -> int:
    return max(len(text) // 4, 1) if text else 0


class FakeProvider:
    """Simulates caching, effort, structured output, a catalog and (optionally) WebSocket mode."""

    name = "fake"

    def __init__(
        self,
        *,
        astra: bool = False,
        websocket: bool = True,
        caching: bool = True,
        credits: bool = False,
        paid: bool = False,
        structured: bool = True,
    ) -> None:
        self.astra = astra
        self.caching = caching
        self.credits = credits
        self.paid = paid
        self.structured = structured
        self._seen: set[str] = set()
        if websocket:
            self.stream_websocket = self._stream_websocket  # type: ignore[assignment]

    def list_models(self) -> list[ModelInfo]:
        models = [
            ModelInfo(id="fake-luna", display_name="Fake Luna", family="luna", supported_efforts=["low", "high"]),
            ModelInfo(id="fake-terra", display_name="Fake Terra", family="terra", supported_efforts=["low", "high"]),
            ModelInfo(id="fake-sol", display_name="Fake Sol", family="sol", supported_efforts=["low", "high"]),
        ]
        if self.astra:
            models.append(ModelInfo(id="fake-astra", display_name="Fake Astra", family="astra"))
        return models

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        return self._respond(request, websocket=False)

    def _stream_websocket(self, request: ChatRequest) -> Iterator[StreamEvent]:
        return self._respond(request, websocket=True)

    def _respond(self, request: ChatRequest, *, websocket: bool) -> Iterator[StreamEvent]:
        if request.structured_output is not None and not self.structured:
            raise UnsupportedCapability("structured output not simulated")
        text = self._text(request)
        prefix_key = hashlib.sha256(request.instructions.encode()).hexdigest()
        prefix_tokens = _tokens(request.instructions)
        history_tokens = sum(_tokens(item.content) for item in request.input)
        cached = prefix_tokens if (self.caching and prefix_key in self._seen and prefix_tokens >= 1024) else 0
        if request.instructions:
            self._seen.add(prefix_key)
        input_tokens = prefix_tokens + history_tokens
        if websocket and len(request.input) > 1:
            # the connection remembers earlier turns, so only the newest item is fresh
            cached = max(cached, input_tokens - _tokens(request.input[-1].content))
        reasoning = {"low": 10, "medium": 60, "high": 200}.get(request.effort or "medium", 60)
        output = _tokens(text) + reasoning
        raw: dict[str, Any] = {
            "input_tokens": input_tokens,
            "input_tokens_details": {"cached_tokens": cached},
            "output_tokens": output,
            "output_tokens_details": {"reasoning_tokens": reasoning},
        }
        if self.credits:
            raw["credits_used"] = 3
        yield TextDelta(text=text)
        yield Completed(
            text=text,
            model=request.model,
            usage=Usage(
                input_tokens=input_tokens,
                cached_input_tokens=cached,
                output_tokens=output,
                reasoning_tokens=reasoning,
                raw=raw,
            ),
        )

    @staticmethod
    def _text(request: ChatRequest) -> str:
        if request.structured_output is not None:
            return json.dumps({"answer": "ok"})
        return "ok"
