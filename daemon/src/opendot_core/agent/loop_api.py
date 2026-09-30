"""Public interface of the agent loop (M2 task 2.2).

The scenario suite (`opendot eval --suite core`) and the API server are written
against this protocol; `agent/loop.py` implements it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Iterator, Literal, Protocol

from pydantic import BaseModel, Field

from ..providers.types import Usage


class TaskState(StrEnum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_TOP_TIER = "waiting_top_tier"
    PAUSED_TASK_BUDGET = "paused_task_budget"
    PAUSED_DAILY_BUDGET = "paused_daily_budget"
    PAUSED_PLAN_LIMIT = "paused_plan_limit"
    PAUSED = "paused"
    """Stopped by the kill switch (the user's Pause) or by anomaly auto-pause."""
    COMPLETED = "completed"
    FAILED = "failed"
    HANDED_OFF = "handed_off"


class TaskRecord(BaseModel):
    id: str
    state: TaskState
    job_type: str = "chat"
    message: str
    credits: float = 0.0
    steps_completed: int = 0
    tool_group: list[str] = Field(default_factory=list)
    prefix_hash: str | None = None
    detail: str = ""


class TextEvent(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolEvent(BaseModel):
    type: Literal["tool"] = "tool"
    tool: str
    behavior: str
    """The rule decision that applied: auto, auto_if_preapproved, ask or handoff."""
    ok: bool = True


class ApprovalEvent(BaseModel):
    type: Literal["approval_required"] = "approval_required"
    approval_id: str
    action_type: str
    review: str = ""
    """The reviewer's note, shown on the approval card."""
    review_verdict: str = "ok"


class PausedEvent(BaseModel):
    type: Literal["paused"] = "paused"
    state: TaskState
    reason: str


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    state: TaskState
    text: str = ""
    model: str = ""
    effort: str | None = None
    credits: float = 0.0
    usage: Usage = Field(default_factory=Usage)


LoopEvent = TextEvent | ToolEvent | ApprovalEvent | PausedEvent | DoneEvent


class AgentLoopProtocol(Protocol):
    def start_task(
        self,
        message: str,
        *,
        job_type: str = "chat",
        chat_id: int | None = None,
        preapproved_actions: frozenset[str] = frozenset(),
    ) -> str:
        """Create a durable task and return its id. Does not call a model."""
        ...

    def run(self, task_id: str) -> Iterator[LoopEvent]:
        """Run steps until the task completes, fails, is handed off, or must wait or pause."""
        ...

    def resume_all(self) -> list[str]:
        """After a restart, continue every unfinished task from its last completed step."""
        ...

    def task(self, task_id: str) -> TaskRecord: ...

    def approve_top_tier(self, task_id: str) -> None:
        """The user allowed this task to use the top tier."""
        ...

    def continue_anyway(self, task_id: str) -> None:
        """The user allowed a task that hit its budget to continue."""
        ...

    def resume_after_plan_limit(self) -> None:
        """The user raised their ChatGPT limit (or waited); clear the plan-request pause."""
        ...

    def task_for_approval(self, approval_id: str) -> str | None:
        """The task waiting on this approval, so the API can run it again once the user decides."""
        ...

    def pause(self, reason: str = "user") -> None:
        """Kill switch: no task sends a model request or runs a tool until ``unpause``."""
        ...

    def unpause(self) -> None: ...

    def paused(self) -> str | None:
        """The kill-switch reason when paused, else None."""
        ...
