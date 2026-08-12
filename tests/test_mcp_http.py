import asyncio
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from alfred.db import Database
from alfred.mcp_server import (
    _BearerAuthMiddleware,
    _bearer_token,
    create_server,
    generate_http_token,
)
from alfred.policy import PolicyStore


def test_bearer_token_parses_the_authorization_header() -> None:
    assert _bearer_token("Bearer abc123") == "abc123"
    assert _bearer_token("bearer abc123") == "abc123"  # case-insensitive scheme
    assert _bearer_token(None) is None
    assert _bearer_token("Basic abc123") is None
    assert _bearer_token("Bearer ") is None
    assert _bearer_token("Bearer   ") is None


def test_generate_http_token_returns_long_unique_values() -> None:
    first, second = generate_http_token(), generate_http_token()
    assert first != second
    assert len(first) >= 32


async def _dummy_asgi_app(scope, receive, send) -> None:
    if scope["type"] == "lifespan":
        await send({"type": "lifespan.startup.complete"})
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _drive(app, scope: dict) -> list[dict]:
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


def _http_scope(*, authorization: str | None) -> dict:
    headers = [(b"authorization", authorization.encode())] if authorization is not None else []
    return {"type": "http", "method": "GET", "path": "/mcp", "headers": headers}


def _status(sent: list[dict]) -> int:
    return next(message["status"] for message in sent if message["type"] == "http.response.start")


def test_bearer_middleware_rejects_a_missing_token() -> None:
    middleware = _BearerAuthMiddleware(_dummy_asgi_app, expected_token="secret")
    sent = asyncio.run(_drive(middleware, _http_scope(authorization=None)))
    assert _status(sent) == 401


def test_bearer_middleware_rejects_the_wrong_token() -> None:
    middleware = _BearerAuthMiddleware(_dummy_asgi_app, expected_token="secret")
    sent = asyncio.run(_drive(middleware, _http_scope(authorization="Bearer wrong")))
    assert _status(sent) == 401


def test_bearer_middleware_accepts_the_correct_token() -> None:
    middleware = _BearerAuthMiddleware(_dummy_asgi_app, expected_token="secret")
    sent = asyncio.run(_drive(middleware, _http_scope(authorization="Bearer secret")))
    assert _status(sent) == 200


def test_bearer_middleware_never_blocks_lifespan_events() -> None:
    """The wrapped app's session manager starts/stops via lifespan events;
    blocking those would silently break every request, auth aside."""
    middleware = _BearerAuthMiddleware(_dummy_asgi_app, expected_token="secret")
    sent = asyncio.run(_drive(middleware, {"type": "lifespan"}))
    assert sent == [{"type": "lifespan.startup.complete"}]


class _ServerThread:
    """Run a real Streamable HTTP server on loopback for one test, then stop it."""

    def __init__(self, app, *, port: int) -> None:
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> "_ServerThread":
        self._thread.start()
        deadline = time.monotonic() + 5
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError("test MCP HTTP server did not start in time")
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


async def _list_tool_names(url: str, *, token: str) -> list[str]:
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=5) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [tool.name for tool in result.tools]


def test_streamable_http_end_to_end_requires_the_configured_token(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    PolicyStore(Database(database_path)).grant(
        client_id="local-mcp",
        allowed_sensitivities={"public", "personal"},
        allowed_tools={"connector_status"},
    )
    token = generate_http_token()
    server = create_server(database_path, client_id="local-mcp")
    app = _BearerAuthMiddleware(server.streamable_http_app(), expected_token=token)

    with _ServerThread(app, port=8799):
        tools = asyncio.run(_list_tool_names("http://127.0.0.1:8799/mcp", token=token))
        assert "connector_status" in tools

        with pytest.raises(Exception):
            asyncio.run(_list_tool_names("http://127.0.0.1:8799/mcp", token="wrong-token"))
