"""Connecting Google and GitHub from the UI (M4 task 4.4)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from starlette.testclient import TestClient

from opendot_core.api.serve_cli import NoTools, build_serve_app
from opendot_core.connections_google import APP_READ_SCOPES, APP_WRITE_SCOPES, GoogleConnectError, GoogleConnector
from opendot_core.db import Database
from opendot_core.eval.fake_provider import ScriptedProvider
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
    # Read-only, and only Gmail's scope: connecting Gmail never asks for Calendar (review S7).
    assert query["scope"][0].split() == list(APP_READ_SCOPES["gmail"])
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [f"{BASE}/oauth/google/callback"]
    assert query["include_granted_scopes"] == ["true"]

    google = FakeGoogle(APP_READ_SCOPES["gmail"])
    _connector(runtime).transport = google.transport()
    done = client.get(f"/oauth/google/callback?state={started['state']}&code=the-code")
    assert done.status_code == 200 and "connected" in done.text
    assert google.requests[0]["code_verifier"] and google.requests[0]["code"] == "the-code"
    assert secrets.values["google-oauth-refresh-token"] == "1//fake-refresh"
    assert "1//fake-refresh" not in done.text

    assert _connector(runtime).app_connected("gmail") and not _connector(runtime).app_connected("google_calendar")
    replay = client.get(f"/oauth/google/callback?state={started['state']}&code=the-code")
    assert replay.status_code == 400  # the state is single-use
    assert client.get("/oauth/google/callback?state=forged&code=x").status_code == 400


def test_write_opt_in_asks_google_for_write_scopes_then_allows_writes(setup) -> None:
    client, runtime, _ = setup
    client.put("/v1/connections/google/client", headers=AUTH, json=CLIENT)
    connector = _connector(runtime)
    for app in ("gmail", "google_calendar"):  # connect both apps, read-only
        _, state = connector.start(app)
        connector.transport = FakeGoogle(APP_READ_SCOPES[app]).transport()
        connector.complete(state=state, code="c")
    assert connector.write_allowed("gmail") is False
    opted = client.put("/v1/connections/gmail/write-opt-in", headers=AUTH, json={"enabled": True}).json()
    assert opted["write_opt_in"] is True and opted["authorize_url"]
    query = parse_qs(urlparse(opted["authorize_url"]).query)
    asked = set(query["scope"][0].split())
    assert set(APP_WRITE_SCOPES["gmail"]) <= asked
    assert not set(APP_WRITE_SCOPES["google_calendar"]) & asked  # only Gmail's write scope (review S9)
    assert connector.write_allowed("gmail") is False  # not until Google grants it
    connector.transport = FakeGoogle(APP_READ_SCOPES["gmail"] + APP_WRITE_SCOPES["gmail"]).transport()
    client.get(f"/oauth/google/callback?state={query['state'][0]}&code=c2")
    assert connector.write_allowed("gmail") is True
    assert connector.write_allowed("google_calendar") is False  # its own switch is still off
    off = client.put("/v1/connections/gmail/write-opt-in", headers=AUTH, json={"enabled": False}).json()
    assert off["write_opt_in"] is False and connector.write_allowed("gmail") is False
    assert client.put("/v1/connections/github/write-opt-in", headers=AUTH, json={"enabled": True}).status_code == 404


def test_disconnecting_one_google_app_keeps_the_other(setup) -> None:
    client, runtime, secrets = setup
    client.put("/v1/connections/google/client", headers=AUTH, json=CLIENT)
    connector = _connector(runtime)
    for app in ("gmail", "google_calendar"):
        _, state = connector.start(app)
        connector.transport = FakeGoogle(APP_READ_SCOPES[app]).transport()
        connector.complete(state=state, code="c")
    connector.disconnect("gmail")
    assert not connector.app_connected("gmail") and connector.app_connected("google_calendar")
    assert secrets.values["google-oauth-refresh-token"] == "1//fake-refresh"
    connector.disconnect("google_calendar")
    assert "google-oauth-refresh-token" not in secrets.values  # nothing left to use the grant


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
    _, state = connector.start("gmail")
    now[0] = 601.0
    with pytest.raises(GoogleConnectError, match="expired"):
        connector.complete(state=state, code="c")


def test_serve_registers_real_syncs_and_disconnects(setup, monkeypatch) -> None:
    client, runtime, secrets = setup
    extras = runtime.app.state.context.extras
    assert set(extras["connector_syncers"]) == {"gmail", "google_calendar", "github"}
    calls: list[str] = []
    import opendot_core.connector_sync as connector_sync

    monkeypatch.setattr(connector_sync, "sync_github", lambda database, store: calls.append("github"))
    extras["connector_syncers"] = connector_sync.build_syncers(runtime.database, secrets)
    extras["connector_syncers"]["github"]()
    assert calls == ["github"]
    secrets.store("github-issue-token", "github_pat_x")
    extras["connector_disconnectors"]["github"]()
    assert "github-issue-token" not in secrets.values
    secrets.store("google-oauth-refresh-token", "1//r")
    extras["connector_disconnectors"]["gmail"]()
    assert "google-oauth-refresh-token" not in secrets.values


def test_weekly_review_gets_pull_requests_only_with_a_github_token() -> None:
    from opendot_core.connector_sync import pull_request_report

    secrets = MemorySecrets()
    assert pull_request_report(secrets) is None
    secrets.store("github-issue-token", "github_pat_x")
    assert callable(pull_request_report(secrets))
