"""Plan one turn deterministically: direct answer, lane, tool group, prompt."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..db import Database
from ..memory_graph import MemoryGraph
from .context import (
    DEFAULT_CONTEXT_CHAR_BUDGET,
    recent_conversation,
    topic_text_for_tools,
)
from .direct import CALENDAR_CONNECTOR, calendar_context_freshness, direct_answer
from .prompt import PromptBuilder
from .tool_groups import is_casual_conversation, select_tool_group


class TurnPlan(BaseModel):
    """Everything decided about a turn before any model is called."""

    request: str
    casual: bool
    #: Set when code can answer alone; no model call is needed.
    direct_answer: str | None = None
    #: The prompt to send; empty when ``direct_answer`` is set.
    prompt: str = ""
    #: The tool group offered to the model. Always empty for a casual turn.
    allowed_tools: frozenset[str] = frozenset()
    context_trace: dict[str, Any] = Field(
        default_factory=lambda: {"sources": [], "freshness": {}, "items": []}
    )


def plan_turn(
    database: Database,
    request: str,
    *,
    chat_id: int,
    external_id: str,
    memory_graph: MemoryGraph | None = None,
    context_char_budget: int = DEFAULT_CONTEXT_CHAR_BUDGET,
    builder: PromptBuilder | None = None,
) -> TurnPlan:
    """Decide how one request is answered: in code, or by a model with a prompt."""
    answer = direct_answer(database, request)
    if answer is not None:
        return TurnPlan(
            request=request,
            casual=False,
            direct_answer=answer,
            context_trace={
                "sources": [CALENDAR_CONNECTOR],
                "freshness": {CALENDAR_CONNECTOR: calendar_context_freshness(database)},
                "items": [],
            },
        )

    builder = builder or PromptBuilder(
        database, memory_graph=memory_graph, context_char_budget=context_char_budget
    )
    initial_history = recent_conversation(
        database, chat_id=chat_id, exclude_external_id=external_id
    )
    recent_topic_text = "\n".join(str(exchange["user"]) for exchange in initial_history)
    casual = is_casual_conversation(request, recent_topic_text=recent_topic_text)
    history = (
        recent_conversation(
            database, chat_id=chat_id, exclude_external_id=external_id, casual=True
        )
        if casual
        else initial_history
    )
    trace: dict[str, Any] = {"sources": [], "freshness": {}, "items": []}
    prompt = builder.build(
        request,
        chat_id=chat_id,
        external_id=external_id,
        history=history,
        trace=trace,
        casual=casual,
    )
    return TurnPlan(
        request=request,
        casual=casual,
        prompt=prompt,
        allowed_tools=(
            frozenset()
            if casual
            else select_tool_group(topic_text_for_tools(request, history))
        ),
        context_trace=trace,
    )
