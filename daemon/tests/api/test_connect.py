"""Connecting Google and GitHub from the UI (M4 task 4.4)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from starlette.testclient import TestClient

from opendot_core.api.serve_cli import NoTools, build_serve_app
from opendot_core.connections_google import GoogleConnectError, GoogleConnector
from opendot_core.db import Database
from opendot_core.eval.fake_provider import ScriptedProvider
from opendot_core.google_oauth import READ_SCOPES, WRITE_SCOPES
from opendot_core.providers.registry import ProviderRegistry, ProviderSettings
from opendot_core.secret_store import SecretStoreError

TOKEN = "connect-test-token-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BASE = "http://127.0.0.1:8765"
CLIENT_ID = "1234-abc.apps.googleusercontent.com"
CLIENT = {"client_id": CLIENT_ID, "client_secret": "GOCSPX-secretvalue"}


class MemorySecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_required(self, name: str) -> str:
        if name not in self.values:
            raise SecretStoreError(name)
        return self.values[name]

    def store(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class FakeGoogle:
    def __init__(self, scopes: tuple[str, ...]) -> None:
        self.scopes = scopes
        self.requests: list[dict[str, str]] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            self.requests.append(form)
            return httpx.Response(
                200,
                json={
                    "access_token": "ya29.fake",
                    "expires_in": 3600,
                    "refresh_token": "1//fake-refresh",
                    "scope": " ".join(self.scopes),
                    "token_type": "Bearer",
                },
            )

        return httpx.MockTransport(handle)


@pytest.fixture
def setup(tmp_path: Path):
    secrets = MemorySecrets()
    registry = ProviderRegistry(ProviderSettings(enabled={"chatgpt_plan"}))
    registry.register(ScriptedProvider([]))
    app, runtime = build_serve_app(
        Database(tmp_path / "opendot.db"), token=TOKEN, ui_dist=None, registry=registry, tools=NoTools(),
        secret_store=secrets,
    )
    return TestClient(app, base_url=BASE), runtime, secrets


def _connector(runtime) -> GoogleConnector:
    return runtime.app.state.context.extras["google_connector"]


def test_google_client_setup_is_keychain_only_and_validated(setup) -> None:
    client, _, secrets = setup
    status = client.get("/v1/connections/google/client", headers=AUTH).json()
    assert status["configured"] is False and len(status["setup_steps"]) >= 4
    bad = client.put("/v1/connections/google/client", headers=AUTH, json={"client_id": "nope", "client_secret": "x"})
    assert bad.status_code == 422
    ok = client.put("/v1/connections/google/client", headers=AUTH, json=CLIENT)
    assert ok.status_code == 200 and ok.json()["configured"] is True
    assert "GOCSPX" not in ok.text and secrets.values["google-oauth-client-secret"] == "GOCSPX-secretvalue"


def test_connect_starts_read_only_with_pkce_and_finishes_through_the_callback(setup) -> None:
    client, runtime, secrets = setup
    assert client.post("/v1/connections/start", headers=AUTH, json={"app": "gmail"}).status_code == 409
    client.put("/v1/connections/google/client", headers=AUTH, json=CLIENT)
    started = client.post("/v1/connections/start", headers=AUTH, json={"app": "gmail"}).json()
    query = parse_qs(urlparse(started["authorize_url"]).query)
    assert query["scope"][0].split() == list(READ_SCOPES)  # read-only by default
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [f"{BASE}/oauth/google/callback"]
    assert query["include_granted_scopes"] == ["true"]

    google = FakeGoogle(READ_SCOPES)
    _connector(runtime).transport = google.transport()
    done = client.get(f"/oauth/google/callback?state={started['state']}&code=the-code")
    assert done.status_code == 200 and "connected" in done.text
    assert google.requests[0]["code_verifier"] and google.requests[0]["code"] == "the-code"
    assert secrets.values["google-oauth-refresh-token"] == "1//fake-refresh"
    assert "1//fake-refresh" not in done.text

    replay = client.get(f"/oauth/google/callback?state={started['state']}&code=the-code")
    assert replay.status_code == 400  # the state is single-use
    assert client.get("/oauth/google/callback?state=forged&code=x").status_code == 400


def test_write_opt_in_asks_google_for_write_scopes_then_allows_writes(setup) -> None:
    client, runtime, _ = setup
    client.put("/v1/connections/google/client", headers=AUTH, json=CLIENT)
    connector = _connector(runtime)
    assert connector.write_allowed() is False
    opted = client.put("/v1/connections/gmail/write-opt-in", headers=AUTH, json={"enabled": True}).json()
    assert opted["write_opt_in"] is True and opted["authorize_url"]
    query = parse_qs(urlparse(opted["authorize_url"]).query)
    assert set(WRITE_SCOPES) <= set(query["scope"][0].split())
    assert connector.write_allowed() is False  # not until Google grants it
    connector.transport = FakeGoogle(READ_SCOPES + WRITE_SCOPES).transport()
    client.get(f"/oauth/google/callback?state={query['state'][0]}&code=c2")
    assert connector.write_allowed() is True
    off = client.put("/v1/connections/gmail/write-opt-in", headers=AUTH, json={"enabled": False}).json()
    assert off["write_opt_in"] is False and connector.write_allowed() is False
    assert client.put("/v1/connections/github/write-opt-in", headers=AUTH, json={"enabled": True}).status_code == 404


def test_github_connects_with_a_token_kept_in_the_keychain(setup) -> None:
    client, _, secrets = setup
    assert client.post("/v1/connections/start", headers=AUTH, json={"app": "github"}).status_code == 400
    assert client.put("/v1/connections/github/token", headers=AUTH, json={"token": "not a token"}).status_code == 422
    saved = client.put("/v1/connections/github/token", headers=AUTH, json={"token": "github_pat_abcdefghijklmnop"})
    assert saved.json() == {"configured": True} and "github_pat" not in saved.text
    assert secrets.values["github-issue-token"] == "github_pat_abcdefghijklmnop"
    assert client.delete("/v1/connections/github/token", headers=AUTH).json() == {"configured": False}
    assert "github-issue-token" not in secrets.values


def test_connect_endpoints_need_the_bearer_token(setup) -> None:
    client, _, _ = setup
    assert client.get("/v1/connections/google/client").status_code == 401
    assert client.put("/v1/connections/github/token", json={"token": "ghp_x"}).status_code == 401


def test_the_callback_refuses_a_foreign_host(setup) -> None:
    client, _, _ = setup
    assert client.get("/oauth/google/callback?state=x&code=y", headers={"Host": "evil.example"}).status_code == 403


def test_pending_sign_ins_expire(tmp_path: Path) -> None:
    secrets = MemorySecrets()
    secrets.store("google-oauth-client-id", CLIENT_ID)
    secrets.store("google-oauth-client-secret", "GOCSPX-secretvalue")
    now = [0.0]
    connector = GoogleConnector(Database(tmp_path / "x.db"), secrets, clock=lambda: now[0])
    _, state = connector.start()
    now[0] = 601.0
    with pytest.raises(GoogleConnectError, match="expired"):
        connector.complete(state=state, code="c")
