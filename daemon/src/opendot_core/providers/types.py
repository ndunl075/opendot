"""Provider-neutral request, response and stream types.

Every provider (ChatGPT plan, API keys, OpenRouter, local) speaks these types,
so the agent loop never depends on one vendor's wire format. They follow the
shape of the Responses API because that is what the default provider needs.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

ReasoningEffort = Literal["low", "medium", "high"]


class ModelInfo(BaseModel):
    """One entry of a provider's model catalog (never hard-coded; see section 7.2)."""

    id: str
    display_name: str | None = None
    family: str | None = None
    """Lower-case family word such as ``luna``, ``terra`` or ``sol``, when the id names one."""
    supported_efforts: list[str] = Field(default_factory=list)


class ToolSpec(BaseModel):
    """A plain function tool. Hosted tools are not supported by the plan flow."""

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class InputItem(BaseModel):
    """One item of the history sent with every request (history is never stored server-side)."""

    role: Literal["user", "assistant", "system", "tool"]
    content: str = ""
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_arguments: str | None = None


class ChatRequest(BaseModel):
    model: str
    instructions: str = ""
    input: list[InputItem]
    tools: list[ToolSpec] = Field(default_factory=list)
    effort: ReasoningEffort | None = None
    structured_output: dict[str, Any] | None = None
    """Optional JSON Schema for a machine step, when the provider accepts structured output."""


class Usage(BaseModel):
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    raw: dict[str, Any] = Field(default_factory=dict)
    """The provider's usage object as received, so credit spending can be inspected."""


class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolCall(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    arguments: str


class Completed(BaseModel):
    """Emitted once, only after the provider confirms the response finished."""

    type: Literal["completed"] = "completed"
    text: str
    model: str
    usage: Usage = Field(default_factory=Usage)
    tool_calls: list[ToolCall] = Field(default_factory=list)


StreamEvent = Annotated[TextDelta | ToolCall | Completed, Field(discriminator="type")]
