"""The production runtime end to end: approve over HTTP -> token escrow -> real loop -> execution.

Built with ``build_agent_runtime``, the same factory the daemon uses (M2 review F2). Only the
outside world is fake: a scripted model and a fake Gmail behind ``GmailActions``.
"""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from opendot_core.action_executor import ActionExecutor
from opendot_core.agent.executor import ApprovalExecutor
from opendot_core.agent.loop_api import TaskState
from opendot_core.agent.runtime import AgentRuntime, build_agent_runtime
from opendot_core.api.escrow import TokenEscrow
from opendot_core.eval.fake_provider import text_turn, tool_turn
from opendot_core.eval.harness import build_world
from opendot_core.gmail import GmailActions
from opendot_core.policy import ApprovalService
from opendot_core.providers.registry import ProviderRegistry, ProviderSettings

TOKEN = "real-loop-bearer-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
DRAFT = {"to": "bob@example.com", "subject": "Lunch", "body": "Free Friday?"}
OK_REVIEW = '{"verdict": "ok", "reasons": []}'


def _runtime(tmp_path: Path, script: list) -> tuple[AgentRuntime, object, TestClient]:
    world = build_world(tmp_path, script=script)
    registry = ProviderRegistry(ProviderSettings(enabled={"chatgpt_plan"}))
    registry.register(world.provider)
    gmail = GmailActions(world.database, ApprovalService(world.database), world.world.gmail)
    runtime = build_agent_runtime(
        world.database, registry, world.tools, api_token=TOKEN, actor=world.tools.actor, execute=gmail.execute,
        clock=world.clock,
    )
    return runtime, world, TestClient(runtime.app)


def test_approving_over_http_executes_the_draft_once(tmp_path: Path) -> None:
    runtime, world, client = _runtime(
        tmp_path, [tool_turn("gmail_draft_create", DRAFT), text_turn(OK_REVIEW), text_turn("Draft is ready.")]
    )
    hub = runtime.app.state.hub

    sent = client.post("/v1/chat/messages", headers=AUTH, json={"text": "draft an email to bob@example.com about lunch"})
    assert sent.status_code == 202
    hub.wait_idle()
    pending = client.get("/v1/approvals", headers=AUTH).json()["approvals"]
    assert len(pending) == 1 and world.world.gmail.create_calls == []
    assert world.provider.requests[1].model == "fake-terra"  # the reviewer's mid-tier pass ran
    approval_id = pending[0]["id"]
    task_id = runtime.loop.task_for_approval(approval_id)

    approved = client.post(f"/v1/approvals/{approval_id}/approve", headers=AUTH, json={})
    assert approved.status_code == 200
    assert runtime.escrow.get(approval_id) not in approved.text  # the UI never sees the token
    hub.wait_idle()
    assert len(world.world.gmail.create_calls) == 1
    assert runtime.loop.task(task_id).state is TaskState.COMPLETED

    replay = client.post(f"/v1/approvals/{approval_id}/approve", headers=AUTH, json={})
    assert replay.status_code == 409
    hub.wait_idle()
    assert len(world.world.gmail.create_calls) == 1


def test_denying_over_http_runs_nothing(tmp_path: Path) -> None:
    runtime, world, client = _runtime(
        tmp_path, [tool_turn("gmail_draft_create", DRAFT), text_turn(OK_REVIEW), text_turn("Okay.")]
    )
    client.post("/v1/chat/messages", headers=AUTH, json={"text": "draft an email to bob@example.com"})
    runtime.app.state.hub.wait_idle()
    approval_id = client.get("/v1/approvals", headers=AUTH).json()["approvals"][0]["id"]
    assert client.post(f"/v1/approvals/{approval_id}/deny", headers=AUTH, json={}).status_code == 200
    runtime.app.state.hub.wait_idle()
    assert world.world.gmail.create_calls == []
    assert runtime.loop.task(runtime.loop.task_for_approval(approval_id)).state is TaskState.COMPLETED


def test_production_default_executes_through_action_executor(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    executor = ApprovalExecutor(ApprovalService(world.database), TokenEscrow(), actor="owner")
    assert isinstance(getattr(executor.execute, "__self__", None), ActionExecutor)


# -- M3: contract gaps found while building the UI ------------------------------------------------


def test_continue_anyway_endpoint_resumes_a_task_over_its_budget(tmp_path: Path) -> None:
    from opendot_core.providers.types import Usage
    from opendot_core.usage.meter import Budgets

    world = build_world(tmp_path, script=[tool_turn("memory_search", {"query": "x"}, usage=Usage(input_tokens=5000)),
                                          text_turn("Done.")])
    registry = ProviderRegistry(ProviderSettings(enabled={"chatgpt_plan"}))
    registry.register(world.provider)
    runtime = build_agent_runtime(world.database, registry, world.tools, api_token=TOKEN, actor=world.tools.actor,
                                  budgets=Budgets(task_credits=0.01, daily_credits=100), clock=world.clock)
    client = TestClient(runtime.app)
    hub = runtime.app.state.hub
    events: list[dict] = []
    original = hub._publish

    def capture(conv: str, msg: str, payload: dict) -> None:
        events.append(payload)
        original(conv, msg, payload)

    hub._publish = capture  # type: ignore[method-assign]
    client.post("/v1/chat/messages", headers=AUTH, json={"text": "what do you know"})
    hub.wait_idle()
    paused = [e for e in events if e["type"] == "paused"]
    assert paused and paused[-1]["reason"] == "task_budget" and paused[-1]["task_id"]
    task_id = paused[-1]["task_id"]

    assert client.post("/v1/tasks/nope/continue", headers=AUTH).status_code == 404
    result = client.post(f"/v1/tasks/{task_id}/continue", headers=AUTH)
    assert result.status_code == 200 and result.json()["task_id"] == task_id
    hub.wait_idle()
    assert runtime.loop.task(task_id).state is TaskState.COMPLETED


def test_approve_top_tier_endpoint(tmp_path: Path) -> None:
    world = build_world(tmp_path, script=[text_turn("deep answer")])
    registry = ProviderRegistry(ProviderSettings(enabled={"chatgpt_plan"}))
    registry.register(world.provider)
    runtime = build_agent_runtime(world.database, registry, world.tools, api_token=TOKEN, clock=world.clock)
    client = TestClient(runtime.app)
    task_id = runtime.loop.start_task("think hard", job_type="deep")
    list(runtime.loop.run(task_id))
    assert runtime.loop.task(task_id).state is TaskState.WAITING_TOP_TIER
    assert client.post(f"/v1/tasks/{task_id}/approve-top-tier", headers=AUTH).status_code == 200
    runtime.app.state.hub.wait_idle()
    assert runtime.loop.task(task_id).state is TaskState.COMPLETED
    assert world.provider.requests[0].model == "fake-sol"


def test_plan_limit_resume_endpoint_reruns_paused_tasks(tmp_path: Path) -> None:
    from opendot_core.eval.fake_provider import error_turn
    from opendot_core.providers.errors import UsageLimitExceeded

    world = build_world(tmp_path, script=[error_turn(UsageLimitExceeded("429")), text_turn("back")])
    registry = ProviderRegistry(ProviderSettings(enabled={"chatgpt_plan"}))
    registry.register(world.provider)
    runtime = build_agent_runtime(world.database, registry, world.tools, api_token=TOKEN, clock=world.clock)
    client = TestClient(runtime.app)
    task_id = runtime.loop.start_task("hello")
    list(runtime.loop.run(task_id))
    assert runtime.loop.task(task_id).state is TaskState.PAUSED_PLAN_LIMIT
    resumed = client.post("/v1/companion/resume-plan-limit", headers=AUTH)
    assert resumed.status_code == 200 and resumed.json()["resumed_task_ids"] == [task_id]
    runtime.app.state.hub.wait_idle()
    assert runtime.loop.task(task_id).state is TaskState.COMPLETED


def test_approval_items_carry_the_reviewer_note(tmp_path: Path) -> None:
    runtime, world, client = _runtime(
        tmp_path, [tool_turn("gmail_draft_create", DRAFT), text_turn('{"verdict": "concern", "reasons": ["Check tone."]}')]
    )
    client.post("/v1/chat/messages", headers=AUTH, json={"text": "draft an email to bob@example.com about lunch"})
    runtime.app.state.hub.wait_idle()
    item = client.get("/v1/approvals", headers=AUTH).json()["approvals"][0]
    assert item["review_verdict"] == "concern" and "Check tone." in item["review_note"]
    again = client.get(f"/v1/approvals/{item['id']}", headers=AUTH).json()
    assert again["review_note"] == item["review_note"]  # durable, not only in the streamed card


def test_replay_never_brings_back_a_decided_approval_or_a_stale_pause(tmp_path: Path) -> None:
    from opendot_core.providers.types import Usage
    from opendot_core.usage.meter import Budgets

    world = build_world(tmp_path, script=[
        tool_turn("gmail_draft_create", DRAFT), text_turn('{"verdict": "ok", "reasons": []}'), text_turn("Drafted."),
        tool_turn("memory_search", {"query": "x"}, usage=Usage(input_tokens=400000)), text_turn("Done."),
    ])
    registry = ProviderRegistry(ProviderSettings(enabled={"chatgpt_plan"}))
    registry.register(world.provider)
    gmail = GmailActions(world.database, ApprovalService(world.database), world.world.gmail)
    runtime = build_agent_runtime(world.database, registry, world.tools, api_token=TOKEN, actor=world.tools.actor,
                                  execute=gmail.execute, budgets=Budgets(task_credits=1.0, daily_credits=100),
                                  clock=world.clock)
    client = TestClient(runtime.app)
    hub = runtime.app.state.hub
    sent = client.post("/v1/chat/messages", headers=AUTH, json={"text": "draft an email to bob@example.com"}).json()
    hub.wait_idle()
    approval_id = client.get("/v1/approvals", headers=AUTH).json()["approvals"][0]["id"]
    client.post(f"/v1/approvals/{approval_id}/approve", headers=AUTH, json={})
    hub.wait_idle()
    replayed = hub.replay(sent["conversation_id"], 0)
    cards = [e["approval"] for e in replayed if e["type"] == "approval_required"]
    assert cards and all(card["status"] == "approved" for card in cards)

    second = client.post("/v1/chat/messages", headers=AUTH, json={"text": "what do you know"}).json()
    hub.wait_idle()
    paused = [e for e in hub.replay(second["conversation_id"], 0) if e["type"] == "paused"]
    assert paused and paused[0]["reason"] == "task_budget"
    client.post(f"/v1/tasks/{paused[0]['task_id']}/continue", headers=AUTH)
    hub.wait_idle()
    assert [e for e in hub.replay(second["conversation_id"], 0) if e["type"] == "paused"] == []
