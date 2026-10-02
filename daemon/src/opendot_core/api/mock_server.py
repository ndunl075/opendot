"""``opendot mock-server``: fake data for every registered endpoint plus a canned chat stream."""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from typing import Any

from pydantic import TypeAdapter, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .contract import ENDPOINTS, STREAM, Endpoint
from .events import ClientFrame, StreamEvent
from .fake_data import fake_json, fake_model
from .models import ApprovalItem, ErrorResponse, OnboardingState, ToolCallRecord, UsageStamp

ALLOWED_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
_CLIENT_FRAME = TypeAdapter(ClientFrame)
_STREAM_EVENT = TypeAdapter(StreamEvent)


def canned_chat(conversation_id: str = "conv_demo", message_id: str = "msg_demo") -> list[dict[str, Any]]:
    """Text deltas, a tool call, an approval card, then completion."""
    base = {"conversation_id": conversation_id, "message_id": message_id}
    approval = fake_model(ApprovalItem).model_copy(update={"conversation_id": conversation_id})
    deltas = ["I can ", "draft that ", "reply for you. ", "It needs your approval ", "before it is created."]
    events: list[dict[str, Any]] = [{"type": "message_started", **base}]
    events += [{"type": "text_delta", "text": text, **base} for text in deltas]
    call = ToolCallRecord(
        call_id="call_demo", name="gmail.create_draft", summary="Create a Gmail draft to venue@example.com",
        status="needs_approval",
    )
    events.append({"type": "tool_call", "call": call.model_dump(mode="json"), **base})
    events.append({"type": "approval_required", "approval": approval.model_dump(mode="json"), **base})
    usage = UsageStamp(model="mock-model-terra", effort="low", credits=0.12, input_tokens=812, output_tokens=64,
                       cached_input_tokens=640)
    events.append({"type": "completed", "usage": usage.model_dump(mode="json"), "text": "".join(deltas), **base})
    out = []
    for seq, event in enumerate(events):
        event = {"seq": seq, **event}
        _STREAM_EVENT.validate_python(event)
        out.append(event)
    return out


def _json(data: Any, status: int = 200) -> Response:
    return JSONResponse(data, status_code=status)


def _error(status: int, code: str, message: str) -> Response:
    return _json(ErrorResponse(code=code, message=message).model_dump(mode="json"), status)


def _handler(endpoint: Endpoint):
    async def handle(request: Request) -> Response:
        if endpoint.request is not None:
            try:
                if endpoint.request_in == "body":
                    raw = await request.body()
                    endpoint.request.model_validate_json(raw or b"{}")
                else:
                    endpoint.request.model_validate(dict(request.query_params))
            except ValidationError as exc:
                return _error(422, "invalid_request", f"{exc.error_count()} validation error(s)")
        if endpoint.name in _ONBOARDING_ENDPOINTS:
            return _json(_onboarding_state(finish=endpoint.name == "onboarding_complete"))
        return _json(fake_json(endpoint.response))

    return handle


_ONBOARDING_ENDPOINTS = {"onboarding_get", "onboarding_complete"}
_onboarding = {"done": False}


def _onboarding_state(*, finish: bool) -> Any:
    """Onboarding is the one flow the UI walks step by step, so the mock remembers finishing it."""
    if finish:
        _onboarding["done"] = True
    state = fake_json(OnboardingState)
    if _onboarding["done"]:
        state.update(
            current_step="done",
            completed_steps=["companion", "chatgpt", "weekly_limit", "connections", "intro", "done"],
            intro_message=state.get("intro_message") or "Hi, I'm Juniper. I'm ready when you are.",
        )
    return state


async def _chat_socket(websocket: WebSocket) -> None:
    # Select the "opendot" subprotocol like the real daemon does; browsers reject a handshake that
    # offered subprotocols and got none back.
    offered = websocket.scope.get("subprotocols", [])
    await websocket.accept(subprotocol="opendot" if "opendot" in offered else None)
    try:
        while True:
            text = await websocket.receive_text()
            try:
                frame = _CLIENT_FRAME.validate_json(text)
            except ValidationError:
                await websocket.send_json(
                    {"type": "error", "seq": 0, "conversation_id": "conv_demo", "message_id": "msg_demo",
                     "code": "invalid_frame", "message": "Unrecognized client frame.", "retryable": False}
                )
                continue
            if frame.type == "send":
                for event in canned_chat(frame.conversation_id or "conv_demo"):
                    await websocket.send_json(event)
            elif frame.type == "resume":
                for event in canned_chat(frame.conversation_id):
                    if event["seq"] > frame.after_seq:
                        await websocket.send_json(event)
    except WebSocketDisconnect:
        return


def create_app() -> Starlette:
    routes: list[Any] = [Route(ep.path, _handler(ep), methods=[ep.method]) for ep in ENDPOINTS]
    routes.append(WebSocketRoute(STREAM.path, _chat_socket))
    return Starlette(routes=routes)


def add_parser(subcommands: Any) -> None:
    mock = subcommands.add_parser("mock-server", help="serve fake data from the API contract (127.0.0.1 only)")
    mock.add_argument("--host", default=ALLOWED_HOST, help="must be 127.0.0.1")
    mock.add_argument("--port", type=int, default=DEFAULT_PORT)
    mock.add_argument("--check", action="store_true", help="self-test every endpoint on an ephemeral port and exit")


def _concrete_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "sample_id", path)


def run_check() -> int:
    import httpx
    import uvicorn
    from websockets.sync.client import connect

    config = uvicorn.Config(create_app(), host=ALLOWED_HOST, port=0, log_level="error", ws="websockets-sansio")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            print("FAIL: mock server did not start", file=sys.stderr)
            return 1
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    base = f"http://{ALLOWED_HOST}:{port}"
    failures: list[str] = []
    try:
        with httpx.Client(base_url=base, timeout=10) as client:
            for ep in ENDPOINTS:
                label = f"{ep.method} {ep.path}"
                try:
                    kwargs: dict[str, Any] = {}
                    if ep.request is not None:
                        sample = fake_model(ep.request).model_dump(mode="json", exclude_none=True)
                        if ep.request_in == "body":
                            kwargs["json"] = sample
                        else:
                            kwargs["params"] = sample
                    response = client.request(ep.method, _concrete_path(ep.path), **kwargs)
                    if response.status_code != 200:
                        raise ValueError(f"HTTP {response.status_code}: {response.text[:120]}")
                    ep.response.model_validate_json(response.content)
                    print(f"OK   {label} -> {ep.response.__name__}")
                except Exception as exc:  # noqa: BLE001 - report every failure, then exit non-zero
                    failures.append(f"{label}: {exc}")
                    print(f"FAIL {label}: {exc}")
        try:
            with connect(f"ws://{ALLOWED_HOST}:{port}{STREAM.path}", open_timeout=10) as ws:
                ws.send(json.dumps({"type": "send", "text": "hello"}))
                seen: list[str] = []
                while "completed" not in seen:
                    event = _STREAM_EVENT.validate_json(ws.recv(timeout=10))
                    seen.append(event.type)
            expected = ["message_started", "text_delta", "tool_call", "approval_required", "completed"]
            if not all(kind in seen for kind in expected) or seen.index("approval_required") > seen.index("completed"):
                raise ValueError(f"unexpected event order: {seen}")
            print(f"OK   WS {STREAM.path} -> {len(seen)} events ({', '.join(dict.fromkeys(seen))})")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"WS {STREAM.path}: {exc}")
            print(f"FAIL WS {STREAM.path}: {exc}")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    if failures:
        print(f"mock-server check failed: {len(failures)} problem(s)", file=sys.stderr)
        return 1
    print(f"mock-server check passed: {len(ENDPOINTS)} endpoints + 1 websocket")
    return 0


def run_mock_server(args: argparse.Namespace) -> int:
    if args.host != ALLOWED_HOST:
        print(f"mock-server only binds {ALLOWED_HOST}; refusing host {args.host!r}", file=sys.stderr)
        return 2
    if args.check:
        return run_check()
    import uvicorn

    print(f"OpenDot mock server on http://{ALLOWED_HOST}:{args.port} (fake data only)")
    uvicorn.run(create_app(), host=ALLOWED_HOST, port=args.port, log_level="warning", ws="websockets-sansio")
    return 0

