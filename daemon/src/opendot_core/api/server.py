"""The real local API server for chat and approvals (M2 task 2.8).

A Starlette app meant to be bound to 127.0.0.1. Every HTTP route and the chat WebSocket
require ``Authorization: Bearer <token>``. The agent loop is used only through
``AgentLoopProtocol``. Approvals come only through the user endpoints below: the model has no
route to them. Approval tokens go into the ``TokenEscrow`` and are never sent to a client.
"""

from __future__ import annotations

import asyncio
import collections
import hashlib
import hmac
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


SOCKET_RECHECK_SECONDS = 15.0


def _socket_credential(ws: Any) -> bytes:
    """The credential a WebSocket connected with (Authorization header or the bearer subprotocol)."""
    header = ws.headers.get("authorization", "")
    prefix, _, rest = header.partition(" ")
    if prefix.lower() == "bearer" and rest.strip():
        return rest.strip().encode()
    return _ws_protocol_token(ws.headers.get("sec-websocket-protocol", "").encode())


def _ws_protocol_token(value: bytes) -> bytes:
    for item in value.split(b","):
        item = item.strip()
        if item.startswith(WS_TOKEN_PREFIX.encode()):
            return item[len(WS_TOKEN_PREFIX) :]
    return b""


CHALLENGE_PATH = "/v1/session/challenge"
SESSION_PATH = "/v1/session"
IDENTITY_LABEL = b"opendot-identity:"
SESSION_LABEL = b"opendot-session:"
CHALLENGE_TTL_SECONDS = 120.0
LOGIN_CODE_TTL_SECONDS = 120.0
SESSION_TTL_SECONDS = 7 * 24 * 3600.0
LOGIN_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: typed by hand from a terminal
REVOKE_PATH = "/v1/session/revoke"


def new_login_code() -> str:
    """Twelve characters from a 32-letter alphabet (60 bits), grouped for typing: ABCD-EFGH-JKLM."""
    raw = "".join(secrets.choice(LOGIN_CODE_ALPHABET) for _ in range(12))
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"


def normalize_login_code(value: str) -> str:
    raw = "".join(ch for ch in value.upper() if ch.isalnum())
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}" if len(raw) == 12 else ""


def identity_proof(token: str, nonce: str) -> str:
    """HMAC-SHA256 of the client's nonce, keyed with the access token. Only the real daemon (which
    knows the token) can produce it; the proof reveals nothing about the token (review S1)."""
    return hmac.new(token.encode(), IDENTITY_LABEL + nonce.encode(), hashlib.sha256).hexdigest()


def session_mac(token: str, challenge: str) -> str:
    """The client's answer to the daemon's challenge: proves it holds the access token without ever
    sending it, so nothing listening on the port can capture it (review S1, S10)."""
    return hmac.new(token.encode(), SESSION_LABEL + challenge.encode(), hashlib.sha256).hexdigest()


class _OneTimeSecrets:
    """Single-use random values that expire (challenges, login codes), kept in memory as hashes."""

    def __init__(
        self, ttl: float, prefix: str, clock: Any = time.monotonic, limit: int = 64, generator: Any = None
    ) -> None:
        self._ttl, self._prefix, self._clock, self._limit = ttl, prefix, clock, limit
        self._generator = generator
        self._items: collections.OrderedDict[bytes, float] = collections.OrderedDict()
        self._lock = threading.Lock()

    def issue(self) -> str:
        value = self._generator() if self._generator else self._prefix + secrets.token_urlsafe(32)
        with self._lock:
            self._items[hashlib.sha256(value.encode()).digest()] = self._clock()
            while len(self._items) > self._limit:
                self._items.popitem(last=False)
        return value

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def take(self, value: str) -> bool:
        """True once, for an issued value that has not expired; it is gone afterwards."""
        digest = hashlib.sha256(value.encode()).digest()
        with self._lock:
            issued = self._items.pop(digest, None)
        return issued is not None and self._clock() - issued <= self._ttl


class SessionTokens:
    """Short-lived credentials for the UI and the desktop shell (security review S1, S8).

    Minted by POST /v1/session only for a client that proved it holds the access token without
    sending it (challenge and HMAC), or that presents a single-use login code made that same way by
    `opendot open`. They live in this process's memory only, so every daemon restart invalidates
    them: a process that takes over the port after the daemon stops can only capture a token that is
    already dead. Stored as hashes."""

    def __init__(self, limit: int = 64, ttl: float = SESSION_TTL_SECONDS, clock: Any = time.monotonic) -> None:
        self._hashes: collections.OrderedDict[bytes, float] = collections.OrderedDict()
        self._limit = limit
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()

    @staticmethod
    def _hash(token: bytes) -> bytes:
        return hashlib.sha256(token).digest()

    def mint(self) -> str:
        token = "ods_" + secrets.token_urlsafe(32)
        with self._lock:
            self._hashes[self._hash(token.encode())] = self._clock()
            while len(self._hashes) > self._limit:
                self._hashes.popitem(last=False)
        return token

    def revoke(self, supplied: bytes) -> None:
        with self._lock:
            self._hashes.pop(self._hash(supplied), None)

    def valid(self, supplied: bytes) -> bool:
        if not supplied.startswith(b"ods_"):
            return False
        digest = self._hash(supplied)
        now = self._clock()
        with self._lock:
            for known, issued in list(self._hashes.items()):
                if now - issued > self._ttl:
                    del self._hashes[known]  # expired (review S12)
                elif secrets.compare_digest(digest, known):
                    return True
        return False


class BearerAuth:
    """Pure ASGI middleware: reject HTTP and WebSocket requests without the right bearer token.

    HTTP uses ``Authorization: Bearer``. The WebSocket accepts that header or, for browsers, the
    token as an offered subprotocol, and also checks the Origin header.
    """

    def __init__(self, app: ASGIApp, token: str, sessions: SessionTokens | None = None) -> None:
        self.app = app
        self._token = token.encode()
        self._sessions = sessions

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and scope.get("path") in (CHALLENGE_PATH, SESSION_PATH):
            # Self-authenticating: the challenge proves who we are; the session endpoint checks an
            # HMAC answer or a single-use login code. Neither ever receives the access token.
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
        long_lived = bool(supplied) and secrets.compare_digest(supplied, self._token)
        accepted = long_lived or (bool(supplied) and self._sessions is not None and self._sessions.valid(supplied))
        if accepted and allowed_origin:
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
        self.recorder: Any = None
        """Optional ``ConversationRecorder`` (api/routes/conversations.py): persists messages as tasks run."""

    def reset(self) -> None:
        """Forget the in-memory replay history (the user reset the companion)."""
        with self._lock:
            self._history.clear()
            self._ids.clear()

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
        self._record(conversation_id, message_id, validated)
        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(sub.queue.put_nowait, validated)
            except RuntimeError:  # the client's event loop already closed
                self.unsubscribe(sub)

    def _record(self, conversation_id: str, message_id: str, event: dict[str, Any]) -> None:
        recorder = self.recorder
        if recorder is None:
            return
        try:
            recorder.on_event(conversation_id, message_id, event)
        except Exception:  # persistence must never break the live stream
            pass

    # -- tasks -------------------------------------------------------------------

    def send(self, text: str, conversation_id: str | None) -> tuple[str, str]:
        """Start a task for a user message and run it in the background."""
        conversation_id = conversation_id or f"conv_{uuid4().hex[:12]}"
        message_id = f"msg_{uuid4().hex[:12]}"
        task_id = self.loop.start_task(text)
        with self._lock:
            self._ids[task_id] = (conversation_id, message_id)
        if self.recorder is not None:
            try:
                self.recorder.add_user_message(conversation_id, message_id, text, task_id)
            except Exception:  # persistence must never stop the task
                pass
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
    database: Any = None,
    registry: Any = None,
    extras: dict[str, Any] | None = None,
    route_modules: list[Any] | None = None,
) -> Starlette:
    """Build the app. Bind it to 127.0.0.1 when serving; every route needs the bearer token."""
    if not token:
        raise ValueError("an API token is required")
    hub = ChatHub(loop, approvals)
    sessions = SessionTokens()
    challenges = _OneTimeSecrets(CHALLENGE_TTL_SECONDS, "odc_")
    login_codes = _OneTimeSecrets(LOGIN_CODE_TTL_SECONDS, "", generator=new_login_code)
    started = time.monotonic()
    created_at = datetime.now(UTC)

    def profile() -> CompanionProfile:
        from .routes._common import settings_for
        from .routes.companion import companion_identity

        reason = loop.paused()
        settings = settings_for(context)
        name, seed, created, style = companion_identity(settings, companion_name, created_at)
        return CompanionProfile(
            name=name, avatar_seed=seed, paused=reason is not None, paused_reason=reason, created_at=created,
            style_preset=style,
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

    async def challenge(request: Request) -> Response:
        nonce = request.query_params.get("nonce", "")
        if not (32 <= len(nonce) <= 128) or not all(ch in "0123456789abcdef" for ch in nonce):
            return _error(400, "invalid_nonce", "Send a fresh random hex nonce (32 to 128 hex digits).")
        return JSONResponse(
            {"daemon": "opendot", "proof": identity_proof(token, nonce), "challenge": challenges.issue()}
        )

    async def session(request: Request) -> Response:
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        code = body.get("login_code")
        if isinstance(code, str) and code:
            # No lockout (review S13: a lockout lets any local process block the owner). A code has
            # 60 bits, works once and lives two minutes, so guessing one is hopeless.
            normalized = normalize_login_code(code)
            if not normalized or not login_codes.take(normalized):
                return _error(401, "invalid_login_code", "That code is wrong, expired or already used. Run `opendot open` again.")
            return JSONResponse({"session_token": sessions.mint()})
        answer, mac = body.get("challenge"), body.get("mac")
        if not (isinstance(answer, str) and isinstance(mac, str)) or not challenges.take(answer):
            return _error(401, "invalid_challenge", "Ask for a fresh challenge first.")
        if not secrets.compare_digest(mac, session_mac(token, answer)):
            return _error(401, "unauthorized", "Wrong answer to the challenge.")
        if body.get("kind") == "login_code":
            return JSONResponse({"login_code": login_codes.issue()})
        return JSONResponse({"session_token": sessions.mint()})

    async def revoke(request: Request) -> Response:
        prefix, _, value = request.headers.get("authorization", "").partition(" ")
        if prefix.lower() == "bearer" and value.strip().startswith("ods_"):
            sessions.revoke(value.strip().encode())
        return JSONResponse({"revoked": True})

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
        credential = _socket_credential(ws)

        def still_allowed() -> bool:
            """A session can expire or be revoked while the socket is open (review S12)."""
            return secrets.compare_digest(credential, token.encode()) or sessions.valid(credential)

        await ws.accept(subprotocol=WS_SUBPROTOCOL if WS_SUBPROTOCOL in offered else None)
        sub = _Subscriber(asyncio.get_running_loop())
        hub.subscribe(sub)

        async def close_unauthorized() -> None:
            try:
                await ws.close(code=1008)
            except RuntimeError:
                pass

        async def pump() -> None:
            while True:
                event = await sub.queue.get()
                if not still_allowed():
                    await close_unauthorized()
                    return
                await ws.send_json(event)

        async def watchdog() -> None:
            while True:
                await asyncio.sleep(SOCKET_RECHECK_SECONDS)
                if not still_allowed():
                    await close_unauthorized()
                    return

        pump_task = asyncio.create_task(pump())
        watchdog_task = asyncio.create_task(watchdog())
        try:
            while True:
                raw = await ws.receive_text()
                if not still_allowed():
                    await close_unauthorized()
                    break
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
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            pump_task.cancel()
            watchdog_task.cancel()
            hub.unsubscribe(sub)

    v = f"/{API_VERSION}"
    routes: list[Route | WebSocketRoute] = [
        Route(f"{v}/health", health, methods=["GET"]),
        Route(CHALLENGE_PATH, challenge, methods=["GET"]),
        Route(SESSION_PATH, session, methods=["POST"]),
        Route(REVOKE_PATH, revoke, methods=["POST"]),
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
    from .routes import ApiContext, build_routes

    context = ApiContext(
        loop=loop, approvals=approvals, rules=rules, escrow=token_escrow, hub=hub, actor=actor, meter=meter,
        database=database, registry=registry, extras=dict(extras or {}),
    )
    context.extras["companion_profile"] = profile
    taken = {(getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", None) or ()))) for route in routes}
    for extra in build_routes(context, route_modules):
        key = (getattr(extra, "path", ""), tuple(sorted(getattr(extra, "methods", None) or ())))
        if key in taken:
            raise ValueError(f"route registered twice: {key}")
        taken.add(key)
        routes.append(extra)
    app = Starlette(routes=routes, middleware=[Middleware(BearerAuth, token=token, sessions=sessions)])
    app.state.sessions = sessions
    app.state.context = context
    app.state.hub = hub
    app.state.token_escrow = token_escrow
    app.state.meter = meter
    return app
