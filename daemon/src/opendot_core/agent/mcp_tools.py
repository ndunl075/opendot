"""The agent's real tools: OpenDot's own MCP tools, called in-process (M4 task 4.4).

``McpTools`` implements the loop's ``ToolExecutor`` over ``mcp_server.create_server`` with the
client id ``agent``, so every call goes through the same PolicyStore checks, previews and
approval tokens as any MCP client. Two things are added for the agent:

- **What each tool is**, declared here instead of guessed from words (M2 review residual risk):
  its rule action, the argument that names its target, the data sensitivity, whether it changes
  anything, and whether it reaches other people. ``reaches_people`` makes the rule engine ask
  every time, whatever a rule says (section 10).
- **Proposals**: tools that only preview (Gmail draft, Calendar event, GitHub issue, sending,
  forgetting, Composio writes) create an approval. ``propose`` returns it for the user to decide.
  When the user's own "always allow" rule makes such a tool ``auto``, ``run`` approves it on the
  user's behalf (the rule is the user's standing decision, never the model's) and executes it
  through ``ActionExecutor``.

``action_commit`` is never offered to the model: the model can never approve or commit anything.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..db import Database
from ..policy import ApprovalService, PolicyStore
from ..providers.types import ToolSpec
from ..rules import ToolIntent

AGENT_CLIENT_ID = "agent"
AGENT_ACTOR = f"mcp:{AGENT_CLIENT_ID}"
#: action_commit: the model can never commit an approval. message_send_propose: sending email is not
#: part of v0.1 (ARCHITECTURE.md section 10: v0.2), and the Gmail drafts switch never covers it
#: (security review S11).
NEVER_OFFERED = frozenset({"action_commit", "message_send_propose"})
#: Offered only while that app's write opt-in is on and Google granted its write scope (review S9).
GOOGLE_WRITE_TOOLS: dict[str, str] = {
    "message_draft": "gmail",
    "calendar_event_propose": "google_calendar",
}


@dataclass(frozen=True)
class ToolFacts:
    action: str
    sensitivity: str = "personal"
    target_arg: str | None = None
    writes: bool = False
    proposes: bool = False
    """Only previews: the call creates an approval and nothing happens until it is approved."""
    reaches_people: bool = False


#: One entry per MCP tool. A tool missing here is not offered to the agent (fail closed).
TOOL_FACTS: dict[str, ToolFacts] = {
    "system_status": ToolFacts("read", "public"),
    "agenda_get": ToolFacts("read"),
    "memory_search": ToolFacts("search"),
    "profile_get": ToolFacts("read"),
    "brief_get": ToolFacts("read"),
    "connector_status": ToolFacts("read", "public"),
    "connector_records_get": ToolFacts("read", "sensitive", "connector"),
    "threads_awaiting_reply": ToolFacts("read", "sensitive"),
    "availability_get": ToolFacts("read"),
    "pull_requests_get": ToolFacts("read"),
    "important_dates_get": ToolFacts("read"),
    "journal_get": ToolFacts("read", "sensitive"),
    "composio_search": ToolFacts("read", "public"),
    "composio_status": ToolFacts("read", "public"),
    "remember": ToolFacts("create", writes=True),
    "memory_correct": ToolFacts("update", target_arg="memory_id", writes=True),
    "memory_feedback": ToolFacts("update", target_arg="memory_id", writes=True),
    "mood_record": ToolFacts("create", "sensitive", writes=True),
    "gratitude_record": ToolFacts("create", writes=True),
    "task_upsert": ToolFacts("create", writes=True),
    "task_complete": ToolFacts("update", target_arg="task_id", writes=True),
    "reminder_set": ToolFacts("create", writes=True),
    "nag_until_done": ToolFacts("create", writes=True),
    "task_schedule": ToolFacts("create", writes=True),
    "important_date_set": ToolFacts("create", writes=True),
    "forget": ToolFacts("delete", target_arg="memory_id", writes=True, proposes=True),
    "calendar_event_propose": ToolFacts("create", target_arg="calendar_id", writes=True, proposes=True),
    "message_draft": ToolFacts("create", target_arg="to", writes=True, proposes=True),
    "message_send_propose": ToolFacts("send", target_arg="to", writes=True, proposes=True, reaches_people=True),
    "github_issue_propose": ToolFacts("create", target_arg="repository", writes=True, proposes=True),
    "composio_connect": ToolFacts("connect", "personal", "toolkit", writes=True, proposes=True),
    "composio_execute": ToolFacts("execute", "personal", None, writes=True, proposes=True),
}


def _run_sync(coroutine: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    # Called from inside an event loop (should not happen: the loop runs tasks on worker threads).
    raise RuntimeError("McpTools must be called from a worker thread, not inside an event loop")


def _arguments(arguments: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(arguments, str):
        return json.loads(arguments) if arguments.strip() else {}
    return dict(arguments)


class McpTools:
    actor = AGENT_ACTOR

    def __init__(self, database_path: Path | str, *, execute: Any = None) -> None:
        from ..mcp_server import MCP_TOOL_NAMES, create_server

        self.database = Database(database_path)
        self.database.migrate()
        self.approvals = ApprovalService(self.database)
        offered = sorted((set(MCP_TOOL_NAMES) & set(TOOL_FACTS)) - NEVER_OFFERED)
        PolicyStore(self.database).grant(
            client_id=AGENT_CLIENT_ID,
            allowed_sensitivities={"public", "personal", "sensitive"},
            allowed_tools=set(offered),
            allow_write=True,
            actor="opendot:runtime",
        )
        self._server = create_server(database_path, client_id=AGENT_CLIENT_ID)
        self._offered = offered
        if execute is None:
            from ..action_executor import ActionExecutor

            execute = ActionExecutor(self.database).execute
        self._execute = execute

    # -- ToolExecutor -------------------------------------------------------------------------

    def offered(self) -> list[str]:
        from ..connections_google import google_write_allowed

        allowed = {app for app in set(GOOGLE_WRITE_TOOLS.values()) if google_write_allowed(self.database, app)}
        return [name for name in self._offered if GOOGLE_WRITE_TOOLS.get(name, "") in allowed or name not in GOOGLE_WRITE_TOOLS]

    def read_only_tools(self) -> frozenset[str]:
        """Tools that only read; the only ones used to fill a task's tool group (security review S4)."""
        return frozenset(name for name in self.offered() if not TOOL_FACTS[name].writes)

    def specs(self) -> list[ToolSpec]:
        offered = set(self.offered())
        tools = _run_sync(self._server.list_tools())
        return [
            ToolSpec(name=tool.name, description=tool.description or "", parameters=tool.inputSchema)
            for tool in tools
            if tool.name in offered
        ]

    def intent(self, name: str, arguments: dict[str, Any] | str) -> ToolIntent:
        facts = TOOL_FACTS.get(name)
        if facts is None or name not in self.offered():
            raise KeyError(name)
        args = _arguments(arguments)
        action = facts.action
        if name == "composio_execute":
            action = str(args.get("slug") or "execute")  # the deny list and send checks read the slug
        target = args.get(facts.target_arg) if facts.target_arg else None
        return ToolIntent(
            tool=name,
            action=action,
            target=None if target is None else str(target),
            sensitivity=facts.sensitivity,
            reaches_people=facts.reaches_people,
        )

    def run(self, name: str, arguments: dict[str, Any] | str, *, task_id: str, call_id: str) -> str:
        facts = TOOL_FACTS[name]
        args = _arguments(arguments)
        if facts.proposes:
            # Only reached through an auto rule the user created ("always allow"): the rule is the
            # user's decision for this exact kind of action, so approve and execute it now.
            approval = self.propose(name, args, task_id=task_id)
            issued = self.approvals.approve(approval.id, actor=self.actor)
            return self._dump(self._execute(approval.id, actor=self.actor, token=issued.token))
        return self._dump(self._call(name, args))

    def propose(self, name: str, arguments: dict[str, Any] | str, *, task_id: str) -> Any:
        facts = TOOL_FACTS.get(name)
        if facts is None or not facts.proposes:
            raise KeyError(f"tool {name!r} has no approval flow")
        result = self._call(name, _arguments(arguments))
        approval_id = result.get("id") if isinstance(result, dict) else None
        approval = self.approvals.get(str(approval_id)) if approval_id else None
        if approval is None:
            raise RuntimeError(f"{name} did not return an approval")
        return approval

    def run_approved(self, approval_id: str) -> str:  # replaced by ApprovalGatedTools in the runtime
        raise KeyError("approved actions run through the approval executor")

    # -- internals ----------------------------------------------------------------------------

    def _call(self, name: str, args: dict[str, Any]) -> Any:
        manager = self._server._tool_manager  # raw Python results, same policy checks as MCP clients
        return _run_sync(manager.call_tool(name, args, context=None, convert_result=False))

    @staticmethod
    def _dump(result: Any) -> str:
        if isinstance(result, str):
            return result
        if hasattr(result, "model_dump"):
            result = result.model_dump(mode="json")
        return json.dumps(result, sort_keys=True, ensure_ascii=False, default=str)


__all__ = ["AGENT_ACTOR", "McpTools", "TOOL_FACTS", "ToolFacts"]
