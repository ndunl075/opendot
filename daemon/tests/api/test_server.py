from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from opendot_core.agent.loop_api import (
    ApprovalEvent,
    DoneEvent,
    LoopEvent,
    PausedEvent,
    TaskRecord,
    TaskState,
    TextEvent,
    ToolEvent,
)
from opendot_core.api.escrow import TokenEscrow
from opendot_core.api.events import CHAT_STREAM_PATH
from opendot_core.api.server import create_app
from opendot_core.db import Database
from opendot_core.policy import ApprovalService
from opendot_core.rules import RuleEngine

TOKEN = "test-bearer-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

PAUSE_CASES = {
    "p_task": (TaskState.PAUSED_TASK_BUDGET, "task_budget"),
    "p_daily": (TaskState.PAUSED_DAILY_BUDGET, "daily_budget"),
    "p_plan": (TaskState.PAUSED_PLAN_LIMIT, "rate_limited"),
    "p_user": (TaskState.PAUSED, "user"),
    "p_top": (TaskState.WAITING_TOP_TIER, "top_tier_approval"),
}


class FakeLoop:
    """Implements AgentLoopProtocol with scripted behavior keyed on the message text."""

    def __init__(self, approvals: ApprovalService) -> None:
        self.approvals = approvals
        self.tasks: dict[str, dict] = {}
        self.by_approval: dict[str, str] = {}
        self._paused: str | None = None
        self.runs: list[str] = []

    def start_task(self, message, *, job_type="chat", chat_id=None, preapproved_actions=frozenset()) -> str:
        task_id = f"task{len(self.tasks) + 1}"
        self.tasks[task_id] = {"message": message, "approval": None}
        return task_id

    def run(self, task_id: str) -> Iterator[LoopEvent]:
        self.runs.append(task_id)
        task = self.tasks[task_id]
        message = task["message"]
        if message in PAUSE_CASES:
            state, _ = PAUSE_CASES[message]
            yield PausedEvent(state=state, reason="because")
            return
        if message == "anomaly":
            self._paused = "anomaly: loop detected"
            yield PausedEvent(state=TaskState.PAUSED, reason="anomaly detected")
            return
        if message == "boom":
            raise RuntimeError("secret detail")
        if message.startswith("needs approval"):
            if task["approval"] is None:
                approval = self.approvals.propose(
                    actor="owner", action_type="gmail_draft_create", preview={"to": "a@example.com", "subject": "Hi"}
                )
                task["approval"] = approval.id
                self.by_approval[approval.id] = task_id
                yield TextEvent(text="Drafting. ")
                yield ApprovalEvent(
                    approval_id=approval.id, action_type="gmail_draft_create", review="looks fine", review_verdict="ok"
                )
                return
            state = self.approvals.get(task["approval"]).state
            yield TextEvent(text="Done." if state == "approved" else "Skipped.")
            yield DoneEvent(state=TaskState.COMPLETED, text="finished " + state, model="m")
            return
        yield TextEvent(text="Hel")
        yield TextEvent(text="lo")
        yield ToolEvent(tool="memory.search", behavior="auto")
        yield DoneEvent(state=TaskState.COMPLETED, text="Hello", model="mini", credits=1.5)

    def resume_all(self) -> list[str]:
        return []

    def task(self, task_id: str) -> TaskRecord:
        return TaskRecord(id=task_id, state=TaskState.RUNNING, message=self.tasks[task_id]["message"])

    def approve_top_tier(self, task_id: str) -> None: ...

    def continue_anyway(self, task_id: str) -> None: ...

    def resume_after_plan_limit(self) -> None: ...

    def task_for_approval(self, approval_id: str) -> str | None:
        return self.by_approval.get(approval_id)

    def pause(self, reason: str = "user") -> None:
        self._paused = reason

    def unpause(self) -> None:
        self._paused = None

    def paused(self) -> str | None:
        return self._paused


@pytest.fixture
def parts(tmp_path: Path):
    db = Database(tmp_path / "opendot.db")
    db.migrate()
    approvals = ApprovalService(db)
    return {
        "loop": FakeLoop(approvals),
        "approvals": approvals,
        "rules": RuleEngine(db),
        "escrow": TokenEscrow(),
    }


@pytest.fixture
def client(parts) -> Iterator[TestClient]:
    app = create_app(
        loop=parts["loop"], approvals=parts["approvals"], rules=parts["rules"], token=TOKEN,
        token_escrow=parts["escrow"],
    )
    with TestClient(app, headers=AUTH) as c:
        yield c


def collect(ws, *, until: set[str]) -> list[dict]:
    events = []
    while True:
        event = ws.receive_json()
        events.append(event)
        if event["type"] in until:
            return events


TERMINAL = {"completed", "paused", "error", "approval_required"}


def request_approval(client: TestClient) -> tuple[str, dict]:
    with client.websocket_connect(CHAT_STREAM_PATH) as ws:
        ws.send_json({"type": "send", "text": "needs approval"})
        events = collect(ws, until={"approval_required"})
    return events[-1]["approval"]["id"], events[-1]


# --- auth ------------------------------------------------------------------------------------


def test_http_requires_bearer_token(parts) -> None:
    app = create_app(loop=parts["loop"], approvals=parts["approvals"], rules=parts["rules"], token=TOKEN,
                     token_escrow=parts["escrow"])
    with TestClient(app) as bare:
        assert bare.get("/v1/health").status_code == 401
        assert bare.get("/v1/approvals", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert bare.get("/v1/approvals", headers={"Authorization": TOKEN}).status_code == 401
        assert bare.post("/v1/chat/messages", json={"text": "hi"}).status_code == 401
        assert bare.get("/v1/approvals", headers=AUTH).status_code == 200
    assert parts["loop"].tasks == {}


def test_websocket_requires_bearer_token(parts) -> None:
    app = create_app(loop=parts["loop"], approvals=parts["approvals"], rules=parts["rules"], token=TOKEN,
                     token_escrow=parts["escrow"])
    with TestClient(app) as bare:
        with pytest.raises(WebSocketDisconnect):
            with bare.websocket_connect(CHAT_STREAM_PATH):
                pass
        with pytest.raises(WebSocketDisconnect):
            with bare.websocket_connect(CHAT_STREAM_PATH, headers={"Authorization": "Bearer wrong"}):
                pass


# --- chat stream -----------------------------------------------------------------------------


def test_send_over_websocket_streams_mapped_events_in_order(client: TestClient) -> None:
    with client.websocket_connect(CHAT_STREAM_PATH) as ws:
        ws.send_json({"type": "send", "text": "hello", "conversation_id": "conv_x"})
        events = collect(ws, until={"completed"})
    assert [e["type"] for e in events] == ["message_started", "text_delta", "text_delta", "tool_call", "completed"]
    assert [e["seq"] for e in events] == sorted({e["seq"] for e in events})
    assert all(e["conversation_id"] == "conv_x" for e in events)
    assert len({e["message_id"] for e in events}) == 1
    assert events[1]["text"] == "Hel" and events[2]["text"] == "lo"
    assert events[3]["call"]["name"] == "memory.search" and events[3]["call"]["status"] == "ok"
    assert events[4]["text"] == "Hello" and events[4]["usage"]["credits"] == 1.5


def test_chat_send_http_streams_to_connected_client(client: TestClient) -> None:
    with client.websocket_connect(CHAT_STREAM_PATH) as ws:
        response = client.post("/v1/chat/messages", json={"text": "hello"})
        assert response.status_code == 202
        body = response.json()
        assert body["stream_path"] == CHAT_STREAM_PATH
        events = collect(ws, until={"completed"})
    assert events[0]["conversation_id"] == body["conversation_id"]
    assert events[0]["message_id"] == body["message_id"]


def test_resume_replays_missed_events(client: TestClient) -> None:
    with client.websocket_connect(CHAT_STREAM_PATH) as ws:
        ws.send_json({"type": "send", "text": "hello", "conversation_id": "conv_r"})
        first = collect(ws, until={"completed"})
        ws.send_json({"type": "resume", "conversation_id": "conv_r", "after_seq": first[1]["seq"]})
        replay = collect(ws, until={"completed"})
    assert [e["seq"] for e in replay] == [e["seq"] for e in first[2:]]


def test_runtime_failure_becomes_error_event_without_detail(client: TestClient) -> None:
    with client.websocket_connect(CHAT_STREAM_PATH) as ws:
        ws.send_json({"type": "send", "text": "boom"})
        events = collect(ws, until={"error"})
    assert events[-1]["code"] == "run_failed"
    assert "secret detail" not in events[-1]["message"]


@pytest.mark.parametrize("message", sorted(PAUSE_CASES))
def test_pause_reason_mapping(client: TestClient, message: str) -> None:
    with client.websocket_connect(CHAT_STREAM_PATH) as ws:
        ws.send_json({"type": "send", "text": message})
        events = collect(ws, until={"paused"})
    assert events[-1]["reason"] == PAUSE_CASES[message][1]


def test_anomaly_pause_mapping(client: TestClient) -> None:
    with client.websocket_connect(CHAT_STREAM_PATH) as ws:
        ws.send_json({"type": "send", "text": "anomaly"})
        events = collect(ws, until={"paused"})
    assert events[-1]["reason"] == "anomaly"


def test_kill_switch_endpoints(client: TestClient, parts) -> None:
    paused = client.post("/v1/companion/pause", json={"reason": "stop"}).json()
    assert paused["paused"] is True and parts["loop"].paused() == "stop"
    assert client.get("/v1/health").json()["paused"] is True
    resumed = client.post("/v1/companion/resume").json()
    assert resumed["paused"] is False and parts["loop"].paused() is None


# --- approvals -------------------------------------------------------------------------------


def test_approval_required_event_and_listing(client: TestClient) -> None:
    approval_id, event = request_approval(client)
    assert event["approval"]["status"] == "pending"
    assert "looks fine" in event["approval"]["preview"]
    listed = client.get("/v1/approvals").json()["approvals"]
    assert [a["id"] for a in listed] == [approval_id]
    assert client.get(f"/v1/approvals/{approval_id}").json()["payload"]["to"] == "a@example.com"
    assert client.get("/v1/approvals/nope").status_code == 404


def test_approve_escrows_token_and_reruns_task(client: TestClient, parts) -> None:
    with client.websocket_connect(CHAT_STREAM_PATH) as ws:
        ws.send_json({"type": "send", "text": "needs approval"})
        approval_id = collect(ws, until={"approval_required"})[-1]["approval"]["id"]
        response = client.post(f"/v1/approvals/{approval_id}/approve", json={})
        assert response.status_code == 200
        assert response.json()["approval"]["status"] == "approved"
        events = collect(ws, until={"completed"})
    token = parts["escrow"].get(approval_id)
    assert token and token.startswith("alf_")
    assert token not in response.text and token not in str(events)
    assert parts["approvals"].get(approval_id).state == "approved"
    assert [e["type"] for e in events] == ["text_delta", "completed"]
    assert events[0]["text"] == "Done."
    assert parts["loop"].runs == ["task1", "task1"]
    assert parts["escrow"].take(approval_id) == token
    assert parts["escrow"].take(approval_id) is None


def test_replayed_approve_is_refused(client: TestClient, parts) -> None:
    approval_id, _ = request_approval(client)
    assert client.post(f"/v1/approvals/{approval_id}/approve", json={}).status_code == 200
    first = parts["escrow"].get(approval_id)
    again = client.post(f"/v1/approvals/{approval_id}/approve", json={})
    assert again.status_code == 409
    assert client.post(f"/v1/approvals/{approval_id}/deny", json={}).status_code == 409
    assert parts["escrow"].get(approval_id) == first  # a refused replay never replaces the token
    assert client.post("/v1/approvals/missing/approve", json={}).status_code == 404


def test_deny_rejects_and_reruns_without_token(client: TestClient, parts) -> None:
    with client.websocket_connect(CHAT_STREAM_PATH) as ws:
        ws.send_json({"type": "send", "text": "needs approval"})
        approval_id = collect(ws, until={"approval_required"})[-1]["approval"]["id"]
        response = client.post(f"/v1/approvals/{approval_id}/deny", json={"reason": "no"})
        assert response.status_code == 200 and response.json()["approval"]["status"] == "denied"
        events = collect(ws, until={"completed"})
    assert parts["approvals"].get(approval_id).state == "rejected"
    assert parts["escrow"].get(approval_id) is None
    assert events[0]["text"] == "Skipped."
    assert client.post(f"/v1/approvals/{approval_id}/approve", json={}).status_code == 409


def test_always_allow_approves_and_creates_rule(client: TestClient, parts) -> None:
    with client.websocket_connect(CHAT_STREAM_PATH) as ws:
        ws.send_json({"type": "send", "text": "needs approval"})
        approval_id = collect(ws, until={"approval_required"})[-1]["approval"]["id"]
        response = client.post(
            f"/v1/approvals/{approval_id}/always-allow", json={"behavior": "auto"}
        )
        assert response.status_code == 200
        body = response.json()
        collect(ws, until={"completed"})
    assert body["approval"]["status"] == "approved"
    rules = {r.id: r for r in parts["rules"].list_rules(include_defaults=False)}
    assert body["created_rule_id"] in rules
    rule = rules[body["created_rule_id"]]
    assert (rule.tool, rule.target, rule.behavior.value) == ("message_draft", "a@example.com", "auto")
    assert parts["escrow"].get(approval_id) is not None


def test_always_allow_honors_preapproved_behavior_and_refuses_replay(client: TestClient, parts) -> None:
    approval_id, _ = request_approval(client)
    body = client.post(f"/v1/approvals/{approval_id}/always-allow", json={}).json()
    rule = next(r for r in parts["rules"].list_rules(include_defaults=False) if r.id == body["created_rule_id"])
    assert rule.behavior.value == "auto_if_preapproved"
    assert client.post(f"/v1/approvals/{approval_id}/always-allow", json={}).status_code == 409


def test_edit_is_not_implemented_and_changes_nothing(client: TestClient, parts) -> None:
    approval_id, _ = request_approval(client)
    response = client.post(f"/v1/approvals/{approval_id}/edit", json={"payload": {"to": "x@example.com"}})
    assert response.status_code == 501
    assert parts["approvals"].get(approval_id).state == "pending"
    assert parts["escrow"].get(approval_id) is None


# -- M2 review F1: browsers cannot set Authorization on a WebSocket ------------------------------


def test_websocket_accepts_the_token_as_a_subprotocol(tmp_path: Path) -> None:
    from opendot_core.api.server import WS_SUBPROTOCOL, WS_TOKEN_PREFIX

    database = Database(tmp_path / "ws.db")
    database.migrate()
    approvals = ApprovalService(database)
    app = create_app(loop=FakeLoop(approvals), approvals=approvals, rules=RuleEngine(database), token=TOKEN,
                     token_escrow=TokenEscrow())
    client = TestClient(app)
    with client.websocket_connect(
        CHAT_STREAM_PATH,
        subprotocols=[WS_SUBPROTOCOL, WS_TOKEN_PREFIX + TOKEN],
        headers={"Origin": "http://127.0.0.1:8765"},
    ) as ws:
        assert ws.accepted_subprotocol == WS_SUBPROTOCOL
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(CHAT_STREAM_PATH, subprotocols=[WS_SUBPROTOCOL, WS_TOKEN_PREFIX + "wrong"]):
            pass


@pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1.evil.example", "null"])
def test_websocket_refuses_foreign_origins_even_with_the_token(tmp_path: Path, origin: str) -> None:
    database = Database(tmp_path / "ws.db")
    database.migrate()
    approvals = ApprovalService(database)
    app = create_app(loop=FakeLoop(approvals), approvals=approvals, rules=RuleEngine(database), token=TOKEN,
                     token_escrow=TokenEscrow())
    with pytest.raises(WebSocketDisconnect):
        with TestClient(app).websocket_connect(CHAT_STREAM_PATH, headers={**AUTH, "Origin": origin}):
            pass


@pytest.mark.parametrize("origin", ["http://localhost:5173", "https://tauri.localhost", "tauri://localhost", "http://[::1]:80"])
def test_local_origins_are_allowed(origin: str) -> None:
    from opendot_core.api.server import origin_allowed

    assert origin_allowed(origin)
