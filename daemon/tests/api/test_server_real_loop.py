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
