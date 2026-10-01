"""The agent's real tools: OpenDot's MCP tools called in-process (M4 task 4.4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opendot_core.agent.loop_api import TaskState
from opendot_core.agent.mcp_tools import AGENT_ACTOR, TOOL_FACTS, McpTools
from opendot_core.agent.runtime import build_agent_runtime
from opendot_core.db import Database
from opendot_core.eval.fake_provider import ScriptedProvider, text_turn, tool_turn
from opendot_core.mcp_server import MCP_TOOL_NAMES
from opendot_core.providers.registry import ProviderRegistry, ProviderSettings
from opendot_core.rules import Behavior, RuleEngine


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "opendot.db"
    Database(path).migrate()
    return path


def test_every_mcp_tool_is_declared_and_action_commit_is_never_offered(db_path: Path) -> None:
    assert set(TOOL_FACTS) | {"action_commit"} == set(MCP_TOOL_NAMES)
    names = [spec.name for spec in McpTools(db_path).specs()]
    assert "action_commit" not in names
    assert "message_draft" in names and "reminder_set" in names
    spec = next(spec for spec in McpTools(db_path).specs() if spec.name == "message_draft")
    assert set(spec.parameters["properties"]) >= {"to", "subject", "body"}


def test_sending_is_declared_as_reaching_people_and_always_asks(db_path: Path) -> None:
    tools = McpTools(db_path)
    rules = RuleEngine(Database(db_path))
    intent = tools.intent("message_send_propose", {"to": "a@b.test", "subject": "s", "body": "b"})
    assert intent.reaches_people and intent.target == "a@b.test"
    rules.add_rule(tool="message_send_propose", action="send", target="a@b.test", behavior="auto")  # beats the default
    decision = rules.decide(intent)
    assert decision.behavior is Behavior.ASK and decision.locked
    assert tools.intent("message_draft", {"to": "x@y.test"}).reaches_people is False


def test_unknown_tools_fail_closed(db_path: Path) -> None:
    with pytest.raises(KeyError):
        McpTools(db_path).intent("action_commit", {})
    with pytest.raises(KeyError):
        McpTools(db_path).intent("format_disk", {})


def test_propose_creates_an_approval_and_touches_nothing(db_path: Path) -> None:
    tools = McpTools(db_path)
    approval = tools.propose("message_draft", {"to": "bob@example.com", "subject": "Hi", "body": "Lunch?"}, task_id="t")
    assert approval.action_type == "gmail_draft_create" and approval.state == "pending"
    assert approval.actor == AGENT_ACTOR
    with pytest.raises(KeyError):
        tools.propose("reminder_set", {}, task_id="t")


def test_auto_run_of_a_proposal_tool_approves_and_executes_once(db_path: Path) -> None:
    executed: list[str] = []

    def execute(approval_id: str, *, actor: str, token: str) -> dict:
        executed.append(approval_id)
        return {"draft_id": "d-1", "replayed": False}

    tools = McpTools(db_path, execute=execute)
    result = json.loads(tools.run("message_draft", {"to": "bob@example.com", "subject": "Hi", "body": "x"},
                                  task_id="t", call_id="c"))
    assert result["draft_id"] == "d-1" and len(executed) == 1


def test_reads_and_local_writes_run_directly(db_path: Path) -> None:
    tools = McpTools(db_path)
    remembered = json.loads(tools.run("remember", {"statement": "Nico likes tea"}, task_id="t", call_id="c1"))
    assert remembered
    found = tools.run("memory_search", {"query": "tea"}, task_id="t", call_id="c2")
    assert "tea" in found


def test_the_runtime_runs_a_real_tool_end_to_end(tmp_path: Path, db_path: Path) -> None:
    provider = ScriptedProvider([tool_turn("remember", {"statement": "Nico prefers mornings"}), text_turn("Noted.")])
    registry = ProviderRegistry(ProviderSettings(enabled={"chatgpt_plan"}))
    registry.register(provider)
    tools = McpTools(db_path)
    runtime = build_agent_runtime(Database(db_path), registry, tools, api_token="t" * 32, actor=tools.actor)
    task_id = runtime.loop.start_task("remember that I prefer mornings")
    list(runtime.loop.run(task_id))
    assert runtime.loop.task(task_id).state is TaskState.COMPLETED
    assert "mornings" in tools.run("memory_search", {"query": "mornings"}, task_id="x", call_id="y")
    sent = provider.requests[0]
    assert len(sent.tools) <= 8 and all(tool.name != "action_commit" for tool in sent.tools)
