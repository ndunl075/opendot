"""The daemon serving the built UI and the API on one loopback origin (M3 task 3.5)."""

from __future__ import annotations

import io
from argparse import Namespace
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from opendot_core.api.serve_cli import NoTools, build_serve_app, load_or_create_token, run_token
from opendot_core.api.web import TOKEN_STORAGE_KEY
from opendot_core.db import Database
from opendot_core.eval.fake_provider import ScriptedProvider, text_turn
from opendot_core.providers.registry import ProviderRegistry, ProviderSettings

TOKEN = "web-test-token-0123456789abcdef"
BASE = "http://127.0.0.1:8765"
INDEX = """<!doctype html><html><head><meta charset="utf-8">
<meta name="opendot-api-token" content="">
<title>OpenDot</title></head><body><div id="root"></div><script src="/assets/app.js"></script></body></html>"""


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_required(self, name: str) -> str:
        from opendot_core.secret_store import SecretStoreError

        if name not in self.values:
            raise SecretStoreError(name)
        return self.values[name]

    def store(self, name: str, value: str) -> None:
        self.values[name] = value


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log('ui')", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return root


def _client(tmp_path: Path, dist: Path | None, script: list | None = None) -> tuple[TestClient, ScriptedProvider]:
    provider = ScriptedProvider(script or [])
    registry = ProviderRegistry(ProviderSettings(enabled={"chatgpt_plan"}))
    registry.register(provider)
    database = Database(tmp_path / "opendot.db")
    app, _ = build_serve_app(database, token=TOKEN, ui_dist=dist, registry=registry)
    return TestClient(app, base_url=BASE), provider


def test_no_cookie_ever_and_no_page_carries_the_token(tmp_path: Path, dist: Path) -> None:
    """Security review S2: cookies are shared by every port on 127.0.0.1, so the token never goes in
    one; the login page keeps it in this origin's localStorage, and no served page contains it."""
    client, _ = _client(tmp_path, dist)
    index = client.get("/")
    assert index.status_code == 200 and TOKEN not in index.text and "set-cookie" not in index.headers
    assert index.headers["cache-control"] == "no-store"
    assert client.get("/rules/123").text == index.text  # SPA fallback, public
    login = client.get("/login")
    assert login.status_code == 200 and TOKEN not in login.text and "set-cookie" not in login.headers
    assert f'localStorage.setItem("{TOKEN_STORAGE_KEY}"' in login.text and "frame-ancestors 'none'" in login.headers[
        "content-security-policy"
    ]
    assert client.post("/login", data={"token": TOKEN}).status_code == 405  # nothing to post a token to
    logout = client.get("/logout")
    assert f'localStorage.removeItem("{TOKEN_STORAGE_KEY}")' in logout.text
    for response in (index, login, logout):
        assert TOKEN not in response.text


def test_identity_proves_the_daemon_knows_the_token_without_revealing_it(tmp_path: Path, dist: Path) -> None:
    """Security review S1: the desktop app checks this before handing the token to whatever answers."""
    import hashlib
    import hmac

    client, _ = _client(tmp_path, dist)
    nonce = "ab" * 16
    response = client.get(f"/v1/identity?nonce={nonce}")  # no bearer token needed
    assert response.status_code == 200
    expected = hmac.new(TOKEN.encode(), b"opendot-identity:" + nonce.encode(), hashlib.sha256).hexdigest()
    assert response.json() == {"daemon": "opendot", "proof": expected}
    assert TOKEN not in response.text
    assert client.get("/v1/identity?nonce=short").status_code == 400
    assert client.get("/v1/identity?nonce=" + "zz" * 16).status_code == 400


def test_assets_are_public_and_confined_to_the_build(tmp_path: Path, dist: Path) -> None:
    client, _ = _client(tmp_path, dist)
    assert client.get("/assets/app.js").text == "console.log('ui')"
    assert client.get("/favicon.svg").status_code == 200
    (dist.parent / "secret.txt").write_text("SENTINEL-OUTSIDE-BUILD", encoding="utf-8")
    for path in ("/assets/../secret.txt", "/assets/%2e%2e/secret.txt", "/assets/..%2fsecret.txt", "/../secret.txt"):
        assert "SENTINEL-OUTSIDE-BUILD" not in client.get(path).text, path
    assert client.get("/assets/index.html").status_code == 404


def test_api_needs_the_bearer_token_not_the_cookie(tmp_path: Path, dist: Path) -> None:
    client, _ = _client(tmp_path, dist)
    client.cookies.set("opendot_session", TOKEN)
    assert client.get("/v1/health").status_code == 401  # a cookie never authorizes the API
    ok = client.get("/v1/health", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200 and ok.json()["status"] == "ok"


def test_unimplemented_contract_endpoints_answer_501_behind_auth(dist: Path) -> None:
    # An API app that serves no contract routes at all, so every contract endpoint is "not implemented"
    # (the real daemon serves /v1/rules and friends now; see test_config_endpoints.py).
    from starlette.applications import Starlette

    from opendot_core.api.web import create_web_app

    client = TestClient(create_web_app(Starlette(routes=[]), token=TOKEN, ui_dist=dist), base_url=BASE)
    assert client.get("/v1/rules").status_code == 401
    missing = client.get("/v1/rules", headers={"Authorization": f"Bearer {TOKEN}"})
    assert missing.status_code == 501 and missing.json()["code"] == "not_implemented"
    assert client.get("/v1/not-in-contract", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example:8765", "192.168.1.5:8765"])
def test_non_loopback_host_headers_are_refused(tmp_path: Path, dist: Path, host: str) -> None:
    client, _ = _client(tmp_path, dist)
    response = client.get("/v1/health", headers={"Host": host, "Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 403
    assert client.get("/login", headers={"Host": host}).status_code == 403


def test_cors_only_for_the_tauri_shell(tmp_path: Path, dist: Path) -> None:
    client, _ = _client(tmp_path, dist)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    tauri = client.get("/v1/health", headers={**auth, "Origin": "http://tauri.localhost"})
    assert tauri.headers.get("access-control-allow-origin") == "http://tauri.localhost"
    preflight = client.options("/v1/health", headers={"Origin": "tauri://localhost"})
    assert preflight.status_code == 204
    other = client.get("/v1/health", headers={**auth, "Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_chat_works_end_to_end_through_the_served_app(tmp_path: Path, dist: Path) -> None:
    client, provider = _client(tmp_path, dist, [text_turn("Hello Nico.")])
    from opendot_core.api.server import WS_SUBPROTOCOL, WS_TOKEN_PREFIX

    with client.websocket_connect(
        "/v1/chat/stream",
        subprotocols=[WS_SUBPROTOCOL, WS_TOKEN_PREFIX + TOKEN],
        headers={"Origin": BASE, "Host": "127.0.0.1:8765"},  # the test client's WebSocket defaults to "testserver"
    ) as ws:
        ws.send_json({"type": "send", "text": "hi"})
        seen = []
        while True:
            event = ws.receive_json()
            seen.append(event["type"])
            if event["type"] in ("completed", "error"):
                break
    assert seen[0] == "message_started" and seen[-1] == "completed"
    assert provider.calls == 1


def test_websocket_refuses_a_foreign_host(tmp_path: Path, dist: Path) -> None:
    client, _ = _client(tmp_path, dist)
    from opendot_core.api.server import WS_SUBPROTOCOL, WS_TOKEN_PREFIX

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/v1/chat/stream", subprotocols=[WS_SUBPROTOCOL, WS_TOKEN_PREFIX + TOKEN], headers={"Host": "evil.example"}
        ):
            pass


def test_missing_ui_build_says_how_to_build_it(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, None)
    page = client.get("/")
    assert page.status_code == 503 and "pnpm -C ui build" in page.text


def test_token_is_created_once_and_can_be_rotated() -> None:
    store = MemoryStore()
    first = load_or_create_token(store)
    assert len(first) >= 32 and load_or_create_token(store) == first
    out = io.StringIO()
    run_token(Namespace(action="show"), store=store, out=out)
    assert out.getvalue().strip() == first
    run_token(Namespace(action="rotate"), store=store, out=io.StringIO())
    assert load_or_create_token(store) != first


def test_no_tools_offers_nothing() -> None:
    tools = NoTools()
    assert tools.specs() == []
    with pytest.raises(KeyError):
        tools.intent("x", {})


def test_serve_and_api_token_are_cli_commands() -> None:
    from opendot_core.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["serve", "--port", "9000"]).port == 9000
    assert parser.parse_args(["api-token", "show"]).action == "show"


def test_token_file_must_be_private_and_complete(tmp_path: Path) -> None:
    import os

    from opendot_core.api.serve_cli import InsecureTokenFile, read_token_file

    good = tmp_path / "token"
    good.write_text("t" * 40, encoding="utf-8")
    if os.name == "posix":
        good.chmod(0o644)
        with pytest.raises(InsecureTokenFile, match="chmod 600"):
            read_token_file(good)
        good.chmod(0o600)
    assert read_token_file(good) == "t" * 40
    short = tmp_path / "short"
    short.write_text("abc", encoding="utf-8")
    if os.name == "posix":
        short.chmod(0o600)
    with pytest.raises(InsecureTokenFile):
        read_token_file(short)
