"""``anthropic_key``: Claude through an Anthropic API key (pay per token, opt-in).

This is the ONLY way OpenDot calls Claude models (ARCHITECTURE.md 6.3). The key
is read from the OS keychain entry ``anthropic_api_key`` and sent only in the
``x-api-key`` header. OpenDot has no Claude.ai login flow and never reads
``~/.claude`` or any other application's credential files.

Uses the Messages API with ``stream=true``. The response is complete only after
``message_stop``. ``effort`` is not sent and structured output is unsupported.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from ._http import HttpProvider, SSEEvent, error_for_status, require_no_structured_output
from .errors import AuthRequired, IncompleteResponse, ProviderError, ProviderUnavailable, RateLimited
from .types import ChatRequest, Completed, StreamEvent, TextDelta, ToolCall, Usage

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096

_STREAM_ERRORS: dict[str, type[ProviderError]] = {
    "authentication_error": AuthRequired,
    "permission_error": AuthRequired,
    "rate_limit_error": RateLimited,
    "overloaded_error": ProviderUnavailable,
    "api_error": ProviderUnavailable,
}


class AnthropicKeyProvider(HttpProvider):
    name = "anthropic_key"
    paid = True
    default_base_url = "https://api.anthropic.com/v1"
    secret_name = "anthropic_api_key"

    def __init__(self, *, max_tokens: int = DEFAULT_MAX_TOKENS, **kwargs) -> None:
        kwargs.pop("base_url", None)  # the key only ever goes to Anthropic
        super().__init__(**kwargs)
        self.max_tokens = max_tokens

    def _headers(self, key: str | None) -> dict[str, str]:
        return {"x-api-key": key or "", "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}

    def _stream_path(self) -> str:
        return "/messages"

    def _payload(self, request: ChatRequest) -> dict[str, Any]:
        require_no_structured_output(request, self.name)
        messages: list[dict[str, Any]] = []
        for item in request.input:
            if item.role == "system":
                continue
            if item.role == "tool":
                block = {"type": "tool_result", "tool_use_id": item.tool_call_id or "", "content": item.content}
                messages.append({"role": "user", "content": [block]})
            elif item.role == "assistant" and item.tool_name:
                try:
                    arguments = json.loads(item.tool_arguments or "{}")
                except ValueError:
                    arguments = {}
                blocks: list[dict[str, Any]] = []
                if item.content:
                    blocks.append({"type": "text", "text": item.content})
                blocks.append(
                    {"type": "tool_use", "id": item.tool_call_id or "", "name": item.tool_name, "input": arguments}
                )
                messages.append({"role": "assistant", "content": blocks})
            else:
                messages.append({"role": item.role, "content": self._redact(item.content)})
        system = "\n\n".join(
            part for part in [request.instructions, *(i.content for i in request.input if i.role == "system")] if part
        )
        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
            "stream": True,
        }
        if system:
            payload["system"] = self._redact(system)
        if request.tools:
            payload["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in request.tools
            ]
        return payload

    def _parse(self, events: Iterator[SSEEvent], request: ChatRequest) -> Iterator[StreamEvent]:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        blocks: dict[int, dict[str, str]] = {}
        model = request.model
        usage: dict[str, Any] = {}
        stopped = False
        for _event, data in events:
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            if not isinstance(chunk, dict):
                continue
            kind = chunk.get("type")
            if kind == "message_start":
                message = chunk.get("message") or {}
                model = message.get("model") or model
                usage.update(message.get("usage") or {})
            elif kind == "content_block_start":
                block = chunk.get("content_block") or {}
                if block.get("type") == "tool_use":
                    blocks[int(chunk.get("index", 0))] = {
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "json": "",
                    }
            elif kind == "content_block_delta":
                delta = chunk.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    text_parts.append(delta["text"])
                    yield TextDelta(text=delta["text"])
                elif delta.get("type") == "input_json_delta":
                    slot = blocks.get(int(chunk.get("index", 0)))
                    if slot is not None:
                        slot["json"] += delta.get("partial_json", "")
            elif kind == "content_block_stop":
                slot = blocks.pop(int(chunk.get("index", 0)), None)
                if slot is not None:
                    call = ToolCall(call_id=slot["id"], name=slot["name"], arguments=slot["json"] or "{}")
                    tool_calls.append(call)
                    yield call
            elif kind == "message_delta":
                usage.update(chunk.get("usage") or {})
            elif kind == "message_stop":
                stopped = True
                break
            elif kind == "error":
                err = chunk.get("error") or {}
                cls = _STREAM_ERRORS.get(err.get("type", ""))
                if cls is None:
                    raise error_for_status(502, str(err.get("message", "")))
                raise cls(str(err.get("message", ""))[:300])
        if not stopped:
            raise IncompleteResponse("the stream ended before the response completed")
        yield Completed(
            text="".join(text_parts),
            model=model,
            usage=Usage(
                input_tokens=int(usage.get("input_tokens") or 0),
                cached_input_tokens=int(usage.get("cache_read_input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                raw=usage,
            ),
            tool_calls=tool_calls,
        )
