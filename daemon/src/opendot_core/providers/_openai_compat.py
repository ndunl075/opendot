"""OpenAI-compatible Chat Completions streaming, shared by openai_key, openrouter and local.

Design choice: these providers use the Chat Completions API
(``POST <base>/chat/completions`` with ``stream=true``), not the Responses
API, because it is the one wire format OpenAI, OpenRouter, Ollama and other
local servers all implement. ``stream_options.include_usage`` asks for a final
usage chunk. A response is complete only after the terminal ``data: [DONE]``
and a ``finish_reason`` were both seen.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from ._http import HttpProvider, SSEEvent, error_for_status
from .errors import IncompleteResponse, ProviderError
from .types import ChatRequest, Completed, StreamEvent, TextDelta, ToolCall, Usage


def _usage(raw: dict[str, Any] | None) -> Usage:
    raw = raw or {}
    prompt_details = raw.get("prompt_tokens_details") or {}
    completion_details = raw.get("completion_tokens_details") or {}
    return Usage(
        input_tokens=int(raw.get("prompt_tokens") or 0),
        cached_input_tokens=int(prompt_details.get("cached_tokens") or 0),
        output_tokens=int(raw.get("completion_tokens") or 0),
        reasoning_tokens=int(completion_details.get("reasoning_tokens") or 0),
        raw=raw,
    )


class OpenAICompatProvider(HttpProvider):
    sends_reasoning_effort = False
    extra_payload: dict[str, Any] = {}

    def _headers(self, key: str | None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _stream_path(self) -> str:
        return "/chat/completions"

    def _payload(self, request: ChatRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.instructions:
            messages.append({"role": "system", "content": self._redact(request.instructions)})
        for item in request.input:
            if item.role == "tool":
                messages.append({"role": "tool", "tool_call_id": item.tool_call_id or "", "content": item.content})
            elif item.role == "assistant" and item.tool_name:
                messages.append(
                    {
                        "role": "assistant",
                        "content": item.content or None,
                        "tool_calls": [
                            {
                                "id": item.tool_call_id or "",
                                "type": "function",
                                "function": {"name": item.tool_name, "arguments": item.tool_arguments or "{}"},
                            }
                        ],
                    }
                )
            else:
                messages.append({"role": item.role, "content": self._redact(item.content)})
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in request.tools
            ]
        if request.effort and self.sends_reasoning_effort:
            payload["reasoning_effort"] = request.effort
        if request.structured_output is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": request.structured_output},
            }
        payload.update(self.extra_payload)
        return payload

    def _parse(self, events: Iterator[SSEEvent], request: ChatRequest) -> Iterator[StreamEvent]:
        text_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        model = request.model
        usage: dict[str, Any] | None = None
        finished = done = False
        for _event, data in events:
            if data.strip() == "[DONE]":
                done = True
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            if not isinstance(chunk, dict):
                continue
            if chunk.get("error"):
                err = chunk["error"]
                code = err.get("code") if isinstance(err, dict) else None
                status = code if isinstance(code, int) else 502
                raise error_for_status(status, str(err.get("message", "")) if isinstance(err, dict) else "")
            model = chunk.get("model") or model
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    text_parts.append(delta["content"])
                    yield TextDelta(text=delta["content"])
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(int(tc.get("index", 0)), {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
                if choice.get("finish_reason"):
                    finished = True
        if not (done and finished):
            raise IncompleteResponse("the stream ended before the response completed")
        tool_calls = [
            ToolCall(call_id=c["id"] or f"call_{i}", name=c["name"], arguments=c["arguments"] or "{}")
            for i, c in sorted(calls.items())
        ]
        yield from tool_calls
        yield Completed(text="".join(text_parts), model=model, usage=_usage(usage), tool_calls=tool_calls)


__all__ = ["OpenAICompatProvider", "ProviderError"]
