"""The real local API server for chat and approvals (M2 task 2.8).

A Starlette app meant to be bound to 127.0.0.1. Every HTTP route and the chat WebSocket
require ``Authorization: Bearer <token>``. The agent loop is used only through
``AgentLoopProtocol``. Approvals come only through the user endpoints below: the model has no
route to them. Approval tokens go into the ``TokenEscrow`` and are never sent to a client.
"""

from __future__ import annotations

import asyncio
import collections
import json
import secrets
import threading
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter, ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketDisconnect

from ..agent.loop_api import (
    AgentLoopProtocol,
    ApprovalEvent,
    DoneEvent,
    LoopEvent,
    TaskState,
    TextEvent,
    ToolEvent,
)
from ..agent.loop_api import PausedEvent as LoopPausedEvent
from ..policy import Approval, ApprovalService, PolicyError
from ..rules import RuleError
from . import events as ev
from .escrow import TokenEscrow
from .events import CHAT_STREAM_PATH, ClientFrame
from .models import (
    API_VERSION,
    ApprovalAlwaysAllowRequest,
    ApprovalApproveRequest,
    ApprovalDecisionResult,
    ApprovalDenyRequest,
    ApprovalItem,
    ApprovalList,
    ApprovalStatus,
    ChatSendAccepted,
    ChatSendRequest,
    CompanionPauseRequest,
    CompanionProfile,
    ErrorResponse,
    HealthResponse,
    PlanLimitResumeResult,
    TaskActionResult,
    UsageStamp,
)

_CLIENT_FRAME = TypeAdapter(ClientFrame)
_STREAM_EVENT = TypeAdapter(ev.StreamEvent)
_HISTORY = 2000

_PAUSE_REASONS: dict[TaskState, str] = {
    TaskState.PAUSED_TASK_BUDGET: "task_budget",
    TaskState.PAUSED_DAILY_BUDGET: "daily_budget",
    TaskState.PAUSED_PLAN_LIMIT: "rate_limited",
    TaskState.WAITING_TOP_TIER: "top_tier_approval",
}
_PAUSED_STATES = frozenset(
    {
        TaskState.PAUSED,
        TaskState.PAUSED_TASK_BUDGET,
        TaskState.PAUSED_DAILY_BUDGET,
        TaskState.PAUSED_PLAN_LIMIT,
        TaskState.WAITING_TOP_TIER,
    }
)
_APPROVAL_STATUS: dict[str, ApprovalStatus] = {
    "pending": "pending",
    "approved": "approved",
    "consumed": "approved",
    "rejected": "denied",
    "expired": "expired",
}


def pause_reason(state: TaskState, detail: str = "", kill_switch: str | None = None) -> str:
    """Map a paused TaskState to the contract's ``paused`` reason."""
    if state in _PAUSE_REASONS:
        return _PAUSE_REASONS[state]
    text = f"{detail} {kill_switch or ''}".lower()
    return "anomaly" if "anomaly" in text else "user"


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(ErrorResponse(code=code, message=message).model_dump(mode="json"), status_code=status)


WS_SUBPROTOCOL = "opendot"
"""The subprotocol the server selects. Browsers cannot set an Authorization header on a WebSocket,
so they offer ``["opendot", "opendot.bearer.<token>"]``; the server answers ``opendot`` and never
echoes the token."""
WS_TOKEN_PREFIX = "opendot.bearer."
ALLOWED_ORIGIN_HOSTS = ("127.0.0.1", "localhost", "[::1]", "tauri.localhost")
"""Browser origins allowed to open the chat WebSocket (the UI served locally, or the Tauri shell).
A WebSocket is not covered by CORS, so without this any web page could try to connect."""


def origin_allowed(origin: str | None) -> bool:
    if origin is None:
        return True  # not a browser (CLI, tests); the bearer token still applies
    scheme, _, rest = origin.partition("://")
    host = rest.split("/", 1)[0]
    host = host.rsplit(":", 1)[0] if not host.endswith("]") else host
    if scheme == "tauri":
        return host == "localhost"
    return scheme in ("http", "https") and host in ALLOWED_ORIGIN_HOSTS


def _ws_protocol_token(value: bytes) -> bytes:
    for item in value.split(b","):
        item = item.strip()
        if item.startswith(WS_TOKEN_PREFIX.encode()):
            return item[len(WS_TOKEN_PREFIX) :]
    return b""


class BearerAuth:
    """Pure ASGI middleware: reject HTTP and WebSocket requests without the right bearer token.

    HTTP uses ``Authorization: Bearer``. The WebSocket accepts that header or, for browsers, the
    token as an offered subprotocol, and also checks the Origin header.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        supplied = b""
        origin: str | None = None
        for key, value in scope.get("headers", []):
            if key == b"authorization" and not supplied:
                prefix, _, rest = value.partition(b" ")
                if prefix.lower() == b"bearer":
                    supplied = rest.strip()
            elif key == b"sec-websocket-protocol" and scope["type"] == "websocket" and not supplied:
                supplied = _ws_protocol_token(value)
            elif key == b"origin":
                origin = value.decode("latin-1")
        allowed_origin = scope["type"] != "websocket" or origin_allowed(origin)
        if supplied and allowed_origin and secrets.compare_digest(supplied, self._token):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = _error(401, "unauthorized", "Missing or invalid bearer token.")
        response.headers["WWW-Authenticate"] = "Bearer"
        await response(scope, receive, send)


class _Subscriber:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()


class ChatHub:
    """Runs tasks on worker threads and fans StreamEvents out to connected WebSocket clients."""

    def __init__(self, loop_: AgentLoopProtocol, approvals: ApprovalService) -> None:
        self.loop = loop_
        self.approvals = approvals
        self._lock = threading.Lock()
        self._seq = 0
        self._history: collections.deque[dict[str, Any]] = collections.deque(maxlen=_HISTORY)
        self._subs: set[_Subscriber] = set()
        self._running: set[str] = set()
        self._ids: dict[str, tuple[str, str]] = {}

    # -- subscribers -------------------------------------------------------------

    def subscribe(self, sub: _Subscriber) -> None:
        with self._lock:
            self._subs.add(sub)

    def unsubscribe(self, sub: _Subscriber) -> None:
        with self._lock:
            self._subs.discard(sub)

    def replay(self, conversation_id: str, after_seq: int) -> list[dict[str, Any]]:
        """Missed events, made current: a replayed approval card shows the approval's status now, and a
        pause whose task is no longer paused is dropped, so nothing stale comes back actionable."""
        with self._lock:
            events = [e for e in self._history if e["seq"] > after_seq and e["conversation_id"] == conversation_id]
        return [fresh for event in events if (fresh := self._current(event)) is not None]

    def _current(self, event: dict[str, Any]) -> dict[str, Any] | None:
        if event["type"] == "approval_required":
            approval = self.approvals.get(event["approval"]["id"])
            if approval is None:
                return event
            current = approval_to_item(approval, self.loop).model_copy(
                update={"conversation_id": event["approval"].get("conversation_id")}
            )
            return {**event, "approval": current.model_dump(mode="json")}
        if event["type"] == "paused" and event.get("task_id"):
            try:
                state = self.loop.task(event["task_id"]).state
            except KeyError:
                return None
            return event if state in _PAUSED_STATES else None
        return event

    def _publish(self, conversation_id: str, message_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "conversation_id": conversation_id, "message_id": message_id, **payload}
            validated = _STREAM_EVENT.validate_python(event).model_dump(mode="json")
            self._history.append(validated)
            subs = list(self._subs)
        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(sub.queue.put_nowait, validated)
            except RuntimeError:  # the client's event loop already closed
                self.unsubscribe(sub)

    # -- tasks -------------------------------------------------------------------

    def send(self, text: str, conversation_id: str | None) -> tuple[str, str]:
        """Start a task for a user message and run it in the background."""
        conversation_id = conversation_id or f"conv_{uuid4().hex[:12]}"
        message_id = f"msg_{uuid4().hex[:12]}"
        task_id = self.loop.start_task(text)
        with self._lock:
            self._ids[task_id] = (conversation_id, message_id)
        self.run_in_background(task_id, announce=True)
        return conversation_id, message_id

    def run_in_background(self, task_id: str, *, announce: bool = False) -> bool:
        with self._lock:
            if task_id in self._running:
                return False
            self._running.add(task_id)
        threading.Thread(target=self._run, args=(task_id, announce), daemon=True).start()
        return True

    def _ids_for(self, task_id: str) -> tuple[str, str]:
        with self._lock:
            if task_id not in self._ids:
                self._ids[task_id] = (f"conv_{uuid4().hex[:12]}", f"msg_{uuid4().hex[:12]}")
            return self._ids[task_id]

    def _run(self, task_id: str, announce: bool) -> None:
        conv, msg = self._ids_for(task_id)
        try:
            if announce:
                self._publish(conv, msg, {"type": "message_started"})
            for event in self.loop.run(task_id):
                for payload in self._map(event, task_id):
                    self._publish(conv, msg, payload)
        except Exception as error:  # the stream must tell the client, not die silently
            self._publish(
                conv, msg,
                {"type": "error", "code": "run_failed", "message": type(error).__name__, "retryable": True},
            )
        finally:
            with self._lock:
                self._running.discard(task_id)

    def wait_idle(self, timeout: float = 5.0) -> None:
        """Block until no task is running (used by tests and shutdown)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._running:
                    return
            time.sleep(0.005)

    # -- LoopEvent -> contract StreamEvent ---------------------------------------

    def _map(self, event: LoopEvent, task_id: str | None = None) -> list[dict[str, Any]]:
        if isinstance(event, TextEvent):
            return [{"type": "text_delta", "text": event.text}]
        if isinstance(event, ToolEvent):
            status = "denied" if event.behavior == "handoff" else ("ok" if event.ok else "error")
            call = {
                "call_id": f"call_{uuid4().hex[:10]}",
                "name": event.tool,
                "summary": f"{event.tool} ({event.behavior})",
                "status": status,
            }
            return [{"type": "tool_call", "call": call}]
        if isinstance(event, ApprovalEvent):
            return [{"type": "approval_required", "approval": self._approval_item(event).model_dump(mode="json")}]
        if isinstance(event, LoopPausedEvent):
            reason = pause_reason(event.state, event.reason, self.loop.paused())
            return [{"type": "paused", "reason": reason, "message": event.reason, "task_id": task_id}]
        if isinstance(event, DoneEvent):
            if event.state in (TaskState.COMPLETED, TaskState.HANDED_OFF):
                usage = UsageStamp(
                    model=event.model or "unknown",
                    effort=event.effort if event.effort in ("low", "medium", "high") else "low",  # type: ignore[arg-type]
                    credits=event.credits,
                    input_tokens=event.usage.input_tokens,
                    output_tokens=event.usage.output_tokens,
                    cached_input_tokens=event.usage.cached_input_tokens,
                )
                return [{"type": "completed", "usage": usage.model_dump(mode="json"), "text": event.text}]
            if event.state == TaskState.FAILED:
                return [{"type": "error", "code": "task_failed", "message": event.text or "The task failed."}]
            reason = pause_reason(event.state, event.text, self.loop.paused())
            return [{"type": "paused", "reason": reason, "message": event.text or event.state.value, "task_id": task_id}]
        return []

    def _approval_item(self, event: ApprovalEvent) -> ApprovalItem:
        approval = self.approvals.get(event.approval_id)
        if approval is not None:
            item = approval_to_item(approval, self.loop)
        else:
            item = ApprovalItem(
                id=event.approval_id, status="pending", action=event.action_type, title=event.action_type,
                preview="", payload={}, created_at=datetime.now(UTC),
            )
        if event.review:
            verdict = event.review_verdict if event.review_verdict in ("ok", "concern", "block") else None
            item = item.model_copy(update={"review_note": event.review, "review_verdict": verdict})
        return item


def approval_to_item(approval: Approval, loop: Any = None) -> ApprovalItem:
    payload = {str(k): v if isinstance(v, str) else json.dumps(v, sort_keys=True) for k, v in approval.preview.items()}
    review = None
    lookup = getattr(loop, "approval_review", None)
    if callable(lookup):
        review = lookup(approval.id)
    note, verdict = review if review else (None, None)
    return ApprovalItem(
        review_note=note,
        review_verdict=verdict if verdict in ("ok", "concern", "block") else None,
        id=approval.id,
        status=_APPROVAL_STATUS.get(approval.state, "pending"),
        action=approval.action_type,
        title=approval.action_type.replace("_", " ").capitalize(),
        preview=json.dumps(approval.preview, sort_keys=True, ensure_ascii=False),
        payload=payload,
        created_at=approval.requested_at,
        expires_at=approval.expires_at,
        decided_at=approval.approved_at,
    )


async def _body(request: Request, model: type[BaseModel]) -> Any:
    raw = await request.body()
    return model.model_validate_json(raw if raw.strip() else b"{}")


def create_app(
    *,
    loop: AgentLoopProtocol,
    approvals: ApprovalService,
    rules: Any,
    token: str,
    token_escrow: TokenEscrow,
    meter: Any = None,
    actor: str = "owner",
    companion_name: str = "OpenDot",
) -> Starlette:
    """Build the app. Bind it to 127.0.0.1 when serving; every route needs the bearer token."""
    if not token:
        raise ValueError("an API token is required")
    hub = ChatHub(loop, approvals)
    started = time.monotonic()
    created_at = datetime.now(UTC)

    def profile() -> CompanionProfile:
        reason = loop.paused()
        return CompanionProfile(
            name=companion_name, avatar_seed="default", paused=reason is not None, paused_reason=reason,
            created_at=created_at, style_preset="concise",
        )

    def bad_request(error: ValidationError) -> JSONResponse:
        return _error(422, "invalid_request", str(error.errors()[0]["msg"]))

    def policy_error(error: PolicyError) -> JSONResponse:
        message = str(error)
        if "does not exist" in message:
            return _error(404, "not_found", message)
        if "expired" in message:
            return _error(410, "approval_expired", message)
        return _error(409, "approval_not_pending", message)

    def approve(approval_id: str) -> Approval:
        """Approve as the user and escrow the one-time token; the token is never returned."""
        token_value = "alf_" + secrets.token_urlsafe(32)
        # Escrow before committing so a crash cannot leave an approved action with no token. A replay
        # must never replace or discard the token an earlier, successful approval stored.
        stored = token_escrow.put_if_absent(approval_id, token_value)
        if not stored:
            # Commit the token the executor will actually be handed, never a different fresh one.
            token_value = token_escrow.get(approval_id) or token_value
        try:
            return approvals.approve_with_token(approval_id, actor=actor, token=token_value).approval
        except BaseException:
            if stored:
                token_escrow.discard(approval_id)
            raise

    def rerun(approval_id: str) -> None:
        task_id = loop.task_for_approval(approval_id)
        if task_id is not None:
            hub.run_in_background(task_id)

    async def health(_: Request) -> Response:
        body = HealthResponse(
            status="ok", uptime_seconds=int(time.monotonic() - started), database_ok=True,
            provider_ok=True, paused=loop.paused() is not None, checked_at=datetime.now(UTC),
        )
        return JSONResponse(body.model_dump(mode="json"))

    async def chat_send(request: Request) -> Response:
        try:
            body: ChatSendRequest = await _body(request, ChatSendRequest)
        except ValidationError as error:
            return bad_request(error)
        conv, msg = await asyncio.to_thread(hub.send, body.text, body.conversation_id)
        accepted = ChatSendAccepted(conversation_id=conv, message_id=msg, stream_path=CHAT_STREAM_PATH)
        return JSONResponse(accepted.model_dump(mode="json"), status_code=202)

    async def approvals_list(_: Request) -> Response:
        items = [approval_to_item(a, loop) for a in await asyncio.to_thread(approvals.list_pending)]
        return JSONResponse(ApprovalList(approvals=items).model_dump(mode="json"))

    async def approval_get(request: Request) -> Response:
        approval = await asyncio.to_thread(approvals.get, request.path_params["approval_id"])
        if approval is None:
            return _error(404, "not_found", "No such approval.")
        return JSONResponse(approval_to_item(approval, loop).model_dump(mode="json"))

    async def approval_approve(request: Request) -> Response:
        approval_id = request.path_params["approval_id"]
        try:
            await _body(request, ApprovalApproveRequest)
            approval = await asyncio.to_thread(approve, approval_id)
        except ValidationError as error:
            return bad_request(error)
        except PolicyError as error:
            return policy_error(error)
        rerun(approval_id)
        result = ApprovalDecisionResult(approval=approval_to_item(approval, loop), executed=False)
        return JSONResponse(result.model_dump(mode="json"))

    async def approval_deny(request: Request) -> Response:
        approval_id = request.path_params["approval_id"]
        try:
            await _body(request, ApprovalDenyRequest)
            approval = await asyncio.to_thread(approvals.reject, approval_id, actor=actor)
        except ValidationError as error:
            return bad_request(error)
        except PolicyError as error:
            return policy_error(error)
        rerun(approval_id)
        result = ApprovalDecisionResult(approval=approval_to_item(approval, loop), executed=False)
        return JSONResponse(result.model_dump(mode="json"))

    async def approval_always_allow(request: Request) -> Response:
        approval_id = request.path_params["approval_id"]
        try:
            body: ApprovalAlwaysAllowRequest = await _body(request, ApprovalAlwaysAllowRequest)
            approval = await asyncio.to_thread(approve, approval_id)
        except ValidationError as error:
            return bad_request(error)
        except PolicyError as error:
            return policy_error(error)
        created_rule_id: str | None = None
        note: str | None = None
        try:
            rule = await asyncio.to_thread(rules.always_allow, approval_id, actor)
            if body.behavior != "auto":
                rule = await asyncio.to_thread(rules.update_rule, rule.id, behavior=body.behavior)
            created_rule_id = rule.id
        except RuleError as error:
            note = f"Approved, but no rule was created: {error}"
        rerun(approval_id)
        result = ApprovalDecisionResult(
            approval=approval_to_item(approval, loop), executed=False, result_summary=note, created_rule_id=created_rule_id
        )
        return JSONResponse(result.model_dump(mode="json"))

    async def approval_edit(_: Request) -> Response:
        return _error(501, "not_implemented", "Editing an approval is not supported yet; deny it and ask again.")

    async def companion_get(_: Request) -> Response:
        return JSONResponse(profile().model_dump(mode="json"))

    async def companion_pause(request: Request) -> Response:
        try:
            body: CompanionPauseRequest = await _body(request, CompanionPauseRequest)
        except ValidationError as error:
            return bad_request(error)
        await asyncio.to_thread(loop.pause, body.reason or "user")
        return JSONResponse(profile().model_dump(mode="json"))

    async def task_action(request: Request, action: str) -> Response:
        task_id = request.path_params["task_id"]
        try:
            await asyncio.to_thread(loop.task, task_id)
        except KeyError:
            return _error(404, "not_found", "No such task.")
        if action == "continue":
            await asyncio.to_thread(loop.continue_anyway, task_id)
            message = "Continuing past the task budget, as you asked."
        else:
            await asyncio.to_thread(loop.approve_top_tier, task_id)
            message = "This task may now use the top-tier model."
        hub.run_in_background(task_id)
        record = await asyncio.to_thread(loop.task, task_id)
        result = TaskActionResult(task_id=task_id, state=record.state.value, message=message)
        return JSONResponse(result.model_dump(mode="json"))

    async def task_continue(request: Request) -> Response:
        return await task_action(request, "continue")

    async def task_approve_top_tier(request: Request) -> Response:
        return await task_action(request, "top_tier")

    async def plan_limit_resume(_: Request) -> Response:
        task_ids = await asyncio.to_thread(loop.resume_after_plan_limit)
        for task_id in task_ids or []:
            hub.run_in_background(task_id)
        return JSONResponse(PlanLimitResumeResult(resumed_task_ids=list(task_ids or [])).model_dump(mode="json"))

    async def companion_resume(_: Request) -> Response:
        await asyncio.to_thread(loop.unpause)
        return JSONResponse(profile().model_dump(mode="json"))

    async def chat_stream(ws: WebSocket) -> None:
        offered = [item.strip() for item in ws.headers.get("sec-websocket-protocol", "").split(",") if item.strip()]
        await ws.accept(subprotocol=WS_SUBPROTOCOL if WS_SUBPROTOCOL in offered else None)
        sub = _Subscriber(asyncio.get_running_loop())
        hub.subscribe(sub)

        async def pump() -> None:
            while True:
                await ws.send_json(await sub.queue.get())

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    frame = _CLIENT_FRAME.validate_json(raw)
                except ValidationError:
                    sub.queue.put_nowait({
                        "type": "error", "code": "bad_frame", "message": "Unrecognized frame.",
                        "retryable": False, "seq": 0, "conversation_id": "", "message_id": "",
                    })
                    continue
                if isinstance(frame, ev.ClientSendFrame):
                    await asyncio.to_thread(hub.send, frame.text, frame.conversation_id)
                elif isinstance(frame, ev.ClientResumeFrame):
                    for missed in hub.replay(frame.conversation_id, frame.after_seq):
                        sub.queue.put_nowait(missed)
                # ping frames need no reply; the socket itself is the keep-alive
        except WebSocketDisconnect:
            pass
        finally:
            pump_task.cancel()
            hub.unsubscribe(sub)

    v = f"/{API_VERSION}"
    routes: list[Route | WebSocketRoute] = [
        Route(f"{v}/health", health, methods=["GET"]),
        Route(f"{v}/chat/messages", chat_send, methods=["POST"]),
        Route(f"{v}/approvals", approvals_list, methods=["GET"]),
        Route(f"{v}/approvals/{{approval_id}}", approval_get, methods=["GET"]),
        Route(f"{v}/approvals/{{approval_id}}/approve", approval_approve, methods=["POST"]),
        Route(f"{v}/approvals/{{approval_id}}/edit", approval_edit, methods=["POST"]),
        Route(f"{v}/approvals/{{approval_id}}/deny", approval_deny, methods=["POST"]),
        Route(f"{v}/approvals/{{approval_id}}/always-allow", approval_always_allow, methods=["POST"]),
        Route(f"{v}/companion", companion_get, methods=["GET"]),
        Route(f"{v}/companion/pause", companion_pause, methods=["POST"]),
        Route(f"{v}/companion/resume", companion_resume, methods=["POST"]),
        Route(f"{v}/companion/resume-plan-limit", plan_limit_resume, methods=["POST"]),
        Route(f"{v}/tasks/{{task_id}}/continue", task_continue, methods=["POST"]),
        Route(f"{v}/tasks/{{task_id}}/approve-top-tier", task_approve_top_tier, methods=["POST"]),
        WebSocketRoute(CHAT_STREAM_PATH, chat_stream),
    ]
    app = Starlette(routes=routes, middleware=[Middleware(BearerAuth, token=token)])
    app.state.hub = hub
    app.state.token_escrow = token_escrow
    app.state.meter = meter
    return app
