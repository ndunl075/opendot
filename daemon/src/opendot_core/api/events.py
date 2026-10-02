"""WebSocket chat stream: server events and client frames.

Both unions are discriminated on ``type``. Every server event carries a monotonic ``seq``
so a reconnecting client can ask to resume after the last sequence it saw.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from .models import ApiModel, ApprovalItem, ToolCallRecord, UsageStamp

CHAT_STREAM_PATH = "/v1/chat/stream"


def assistant_id(message_id: str) -> str:
    """The id of the assistant reply to the user message ``message_id``.

    Stream events carry this id, and the stored reply uses it, so a client never mistakes the reply
    for the user's own message (which keeps ``message_id``).
    """
    return f"{message_id}_a"


class _Event(ApiModel):
    seq: int = Field(ge=0)
    conversation_id: str
    message_id: str


class MessageStartedEvent(_Event):
    type: Literal["message_started"] = "message_started"


class TextDeltaEvent(_Event):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolCallEvent(_Event):
    type: Literal["tool_call"] = "tool_call"
    call: ToolCallRecord


class ApprovalRequiredEvent(_Event):
    type: Literal["approval_required"] = "approval_required"
    approval: ApprovalItem


class CompletedEvent(_Event):
    type: Literal["completed"] = "completed"
    usage: UsageStamp
    text: str


class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    code: str
    message: str
    retryable: bool = False


class PausedEvent(_Event):
    type: Literal["paused"] = "paused"
    reason: Literal["user", "task_budget", "daily_budget", "rate_limited", "anomaly", "top_tier_approval"]
    message: str
    resume_at: str | None = None
    """ISO timestamp when the pause lifts on its own, when known."""
    task_id: str | None = None
    """The paused task, for "continue anyway" (task_budget) and "allow top tier" (top_tier_approval)."""


StreamEvent = Annotated[
    MessageStartedEvent
    | TextDeltaEvent
    | ToolCallEvent
    | ApprovalRequiredEvent
    | CompletedEvent
    | ErrorEvent
    | PausedEvent,
    Field(discriminator="type"),
]


class ClientSendFrame(ApiModel):
    type: Literal["send"] = "send"
    text: str = Field(min_length=1)
    conversation_id: str | None = None


class ClientResumeFrame(ApiModel):
    type: Literal["resume"] = "resume"
    conversation_id: str
    after_seq: int = Field(ge=0)


class ClientPingFrame(ApiModel):
    type: Literal["ping"] = "ping"


ClientFrame = Annotated[
    ClientSendFrame | ClientResumeFrame | ClientPingFrame,
    Field(discriminator="type"),
]

EVENT_MODELS = (
    MessageStartedEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ApprovalRequiredEvent,
    CompletedEvent,
    ErrorEvent,
    PausedEvent,
)
CLIENT_FRAME_MODELS = (ClientSendFrame, ClientResumeFrame, ClientPingFrame)
