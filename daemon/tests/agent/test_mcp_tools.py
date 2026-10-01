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


def allow_google_writes(path: Path) -> None:
    from opendot_core.google_oauth import READ_SCOPES, WRITE_SCOPES
    from opendot_core.settings_store import SettingsStore

    SettingsStore(Database(path)).set(
        "google_connection",
        {
            "apps": ["gmail", "google_calendar"],
            "write_opt_in": {"gmail": True, "google_calendar": True},
            "granted_scopes": [*READ_SCOPES, *WRITE_SCOPES],
        },
    )


def test_google_write_tools_are_hidden_until_the_user_opts_in(db_path: Path) -> None:
    names = {spec.name for spec in McpTools(db_path).specs()}
    assert not names & {"message_draft", "message_send_propose", "calendar_event_propose"}
    with pytest.raises(KeyError):
        McpTools(db_path).intent("message_draft", {"to": "x@y.test"})
    allow_google_writes(db_path)
    assert {"message_draft", "calendar_event_propose"} <= {spec.name for spec in McpTools(db_path).specs()}


def test_every_mcp_tool_is_declared_and_action_commit_is_never_offered(db_path: Path) -> None:
    allow_google_writes(db_path)
    assert set(TOOL_FACTS) | {"action_commit"} == set(MCP_TOOL_NAMES)
    names = [spec.name for spec in McpTools(db_path).specs()]
    assert "action_commit" not in names
    assert "message_draft" in names and "reminder_set" in names
    spec = next(spec for spec in McpTools(db_path).specs() if spec.name == "message_draft")
    assert set(spec.parameters["properties"]) >= {"to", "subject", "body"}


def test_sending_is_declared_as_reaching_people_and_always_asks(db_path: Path) -> None:
    allow_google_writes(db_path)
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
    allow_google_writes(db_path)
    tools = McpTools(db_path)
    approval = tools.propose("message_draft", {"to": "bob@example.com", "subject": "Hi", "body": "Lunch?"}, task_id="t")
    assert approval.action_type == "gmail_draft_create" and approval.state == "pending"
    assert approval.actor == AGENT_ACTOR
    with pytest.raises(KeyError):
        tools.propose("reminder_set", {}, task_id="t")


def test_auto_run_of_a_proposal_tool_approves_and_executes_once(db_path: Path) -> None:
    allow_google_writes(db_path)
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


def test_s4_unrequested_write_tools_are_never_offered(db_path: Path) -> None:
    from opendot_core.agent.tool_groups import choose_task_tool_group

    tools = McpTools(db_path)
    available = [spec.name for spec in tools.specs()]
    read_only = tools.read_only_tools()
    assert "task_schedule" not in read_only and "agenda_get" in read_only
    group = choose_task_tool_group("what's on my calendar today?", available, read_only)
    writes = {name for name in group if TOOL_FACTS[name].writes}
    assert writes == set(), f"unrequested write tools offered: {writes}"
    asked = choose_task_tool_group("remind me to stretch at 5", available, read_only)
    assert "reminder_set" in asked  # a write the user asked for is still offered


def test_s9_each_google_app_write_switch_offers_only_its_own_tools(db_path: Path) -> None:
    from opendot_core.google_oauth import READ_SCOPES, WRITE_SCOPES
    from opendot_core.settings_store import SettingsStore

    SettingsStore(Database(db_path)).set(
        "google_connection",
        {
            "apps": ["gmail", "google_calendar"],
            "write_opt_in": {"gmail": True},
            "granted_scopes": [*READ_SCOPES, *WRITE_SCOPES],
        },
    )
    names = {spec.name for spec in McpTools(db_path).specs()}
    assert {"message_draft", "message_send_propose"} <= names
    assert "calendar_event_propose" not in names


def test_s4_the_production_wrapper_keeps_unrequested_writes_out(tmp_path: Path, db_path: Path) -> None:
    """The runtime wraps McpTools in ApprovalGatedTools; the read-only list must survive that."""
    provider = ScriptedProvider([text_turn("ok")])
    registry = ProviderRegistry(ProviderSettings(enabled={"chatgpt_plan"}))
    registry.register(provider)
    tools = McpTools(db_path)
    runtime = build_agent_runtime(Database(db_path), registry, tools, api_token="t" * 32, actor=tools.actor)
    assert runtime.tools.read_only_tools() == tools.read_only_tools()
    task_id = runtime.loop.start_task("what's on my calendar today?")
    group = runtime.loop.task(task_id).tool_group
    assert group and not [name for name in group if TOOL_FACTS[name].writes]


def test_s4_small_declared_sets_are_filtered_too() -> None:
    from opendot_core.agent.tool_groups import choose_task_tool_group

    group = choose_task_tool_group("hello there", ["agenda_get", "task_schedule"], frozenset({"agenda_get"}))
    assert group == ["agenda_get"]
