"""The API server driving the real AgentLoop: approve over HTTP -> token escrow -> execution (M2 review F2)."""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from opendot_core.agent.executor import ApprovalExecutor
from opendot_core.agent.loop_api import TaskState
from opendot_core.api.escrow import TokenEscrow
from opendot_core.api.server import create_app
from opendot_core.eval.fake_provider import text_turn, tool_turn
from opendot_core.eval.harness import Stack, build_stack
from opendot_core.gmail import GmailActions

TOKEN = "real-loop-bearer-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
DRAFT = {"to": "bob@example.com", "subject": "Lunch", "body": "Free Friday?"}


def _app(stack: Stack) -> tuple[TestClient, TokenEscrow]:
    escrow = TokenEscrow()
    gmail = GmailActions(stack.database, stack.approvals, stack.world.gmail)
    executor = ApprovalExecutor(
        stack.approvals, escrow, actor=stack.tools.actor, handlers={GmailActions.action_type: gmail.execute}
    )
    stack.tools.run_approved = executor.run_approved  # type: ignore[method-assign]
    app = create_app(
        loop=stack.loop, approvals=stack.approvals, rules=stack.rules, token=TOKEN, token_escrow=escrow,
        actor=stack.tools.actor,
    )
    return TestClient(app), escrow


def test_approving_over_http_executes_the_draft_once(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("gmail_draft_create", DRAFT), text_turn("Draft is ready.")])
    client, escrow = _app(stack)
    hub = client.app.state.hub

    sent = client.post("/v1/chat/messages", headers=AUTH, json={"text": "draft an email to bob@example.com about lunch"})
    assert sent.status_code == 202
    hub.wait_idle()
    pending = client.get("/v1/approvals", headers=AUTH).json()["approvals"]
    assert len(pending) == 1 and stack.world.gmail.create_calls == []
    approval_id = pending[0]["id"]
    task_id = stack.loop.task_for_approval(approval_id)

    approved = client.post(f"/v1/approvals/{approval_id}/approve", headers=AUTH, json={})
    assert approved.status_code == 200
    assert escrow.get(approval_id) not in approved.text  # the UI never sees the token
    hub.wait_idle()
    assert len(stack.world.gmail.create_calls) == 1
    assert stack.loop.task(task_id).state is TaskState.COMPLETED

    replay = client.post(f"/v1/approvals/{approval_id}/approve", headers=AUTH, json={})
    assert replay.status_code == 409
    hub.wait_idle()
    assert len(stack.world.gmail.create_calls) == 1


def test_denying_over_http_runs_nothing(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("gmail_draft_create", DRAFT), text_turn("Okay.")])
    client, _ = _app(stack)
    client.post("/v1/chat/messages", headers=AUTH, json={"text": "draft an email to bob@example.com"})
    client.app.state.hub.wait_idle()
    approval_id = client.get("/v1/approvals", headers=AUTH).json()["approvals"][0]["id"]
    assert client.post(f"/v1/approvals/{approval_id}/deny", headers=AUTH, json={}).status_code == 200
    client.app.state.hub.wait_idle()
    assert stack.world.gmail.create_calls == []
    assert stack.loop.task(stack.loop.task_for_approval(approval_id)).state is TaskState.COMPLETED
