import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from alfred.google_oauth import (
    GoogleOAuthClient,
    GoogleOAuthError,
    LocalRedirectListener,
    authorize_interactively,
    build_authorization_url,
)


def test_authorization_url_includes_offline_consent_and_scopes() -> None:
    url = build_authorization_url(
        client_id="CLIENT_ID",
        redirect_uri="http://127.0.0.1:8765",
        scopes=("scope-a", "scope-b"),
        state="STATE",
    )

    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=CLIENT_ID" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "scope=scope-a+scope-b" in url
    assert "state=STATE" in url


def test_local_redirect_listener_captures_the_authorization_code() -> None:
    listener = LocalRedirectListener(port=8766)

    def fake_browser() -> None:
        time.sleep(0.1)
        httpx.get(listener.redirect_uri, params={"code": "auth-code", "state": "expected-state"})

    threading.Thread(target=fake_browser, daemon=True).start()
    result = listener.wait_for_redirect(timeout_seconds=5)

    assert result.code == "auth-code"
    assert result.state == "expected-state"
    assert result.error is None


def test_local_redirect_listener_captures_a_denied_consent() -> None:
    listener = LocalRedirectListener(port=8767)

    def fake_browser() -> None:
        time.sleep(0.1)
        httpx.get(listener.redirect_uri, params={"error": "access_denied", "state": "s"})

    threading.Thread(target=fake_browser, daemon=True).start()
    result = listener.wait_for_redirect(timeout_seconds=5)

    assert result.code is None
    assert result.error == "access_denied"


def test_local_redirect_listener_times_out_without_a_request() -> None:
    listener = LocalRedirectListener(port=8768)

    with pytest.raises(GoogleOAuthError, match="timed out"):
        listener.wait_for_redirect(timeout_seconds=0.2)


def test_oauth_client_exchanges_code_for_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/token" or "token" in str(request.url)
        body = request.read().decode("utf-8")
        assert "grant_type=authorization_code" in body
        assert "code=auth-code" in body
        return httpx.Response(200, json={"access_token": "A", "expires_in": 3600, "refresh_token": "R", "scope": "s"})

    client = GoogleOAuthClient("CLIENT_ID", "CLIENT_SECRET", transport=httpx.MockTransport(handler))
    try:
        token = client.exchange_code("auth-code", redirect_uri="http://127.0.0.1:8765")
    finally:
        client.close()
    assert (token.access_token, token.refresh_token) == ("A", "R")


def test_oauth_client_refreshes_access_token_without_a_new_refresh_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode("utf-8")
        assert "grant_type=refresh_token" in body
        assert "refresh_token=stored-refresh-token" in body
        assert "scope=" not in body
        return httpx.Response(200, json={"access_token": "FRESH", "expires_in": 3600})

    client = GoogleOAuthClient("CLIENT_ID", "CLIENT_SECRET", transport=httpx.MockTransport(handler))
    try:
        token = client.refresh_access_token("stored-refresh-token")
    finally:
        client.close()
    assert token.access_token == "FRESH"
    assert token.refresh_token is None


def test_oauth_client_can_downscope_a_refresh_to_a_subset_of_the_grant() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode("utf-8")
        assert "grant_type=refresh_token" in body
        assert "scope=health-a+health-b" in body or "scope=health-a%20health-b" in body
        return httpx.Response(200, json={"access_token": "HEALTH", "expires_in": 3600, "scope": "health-a health-b"})

    client = GoogleOAuthClient("CLIENT_ID", "CLIENT_SECRET", transport=httpx.MockTransport(handler))
    try:
        token = client.refresh_access_token("stored-refresh-token", scopes=("health-a", "health-b"))
    finally:
        client.close()
    assert token.access_token == "HEALTH"


def test_oauth_client_raises_on_an_error_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = GoogleOAuthClient("CLIENT_ID", "CLIENT_SECRET", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(GoogleOAuthError, match="400"):
            client.refresh_access_token("stale-token")
    finally:
        client.close()


def test_authorize_interactively_completes_the_full_loopback_flow() -> None:
    def token_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "A", "expires_in": 3600, "refresh_token": "R"})

    captured_urls: list[str] = []
    redirect_uri = "http://127.0.0.1:8769"

    def simulate_browser(url: str) -> None:
        captured_urls.append(url)
        state = parse_qs(urlparse(url).query)["state"][0]

        def visit() -> None:
            time.sleep(0.1)
            httpx.get(redirect_uri, params={"code": "abc", "state": state})

        threading.Thread(target=visit, daemon=True).start()

    token = authorize_interactively(
        client_id="CLIENT_ID",
        client_secret="CLIENT_SECRET",
        port=8769,
        open_browser=False,
        on_url=simulate_browser,
        transport=httpx.MockTransport(token_handler),
    )

    assert token.access_token == "A"
    assert token.refresh_token == "R"
    assert captured_urls  # the caller was given a URL to show the user


def test_authorize_interactively_rejects_a_state_mismatch() -> None:
    def token_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("token exchange must not happen on a state mismatch")

    redirect_uri = "http://127.0.0.1:8770"

    def simulate_browser(url: str) -> None:
        def visit() -> None:
            time.sleep(0.1)
            httpx.get(redirect_uri, params={"code": "abc", "state": "wrong-state"})

        threading.Thread(target=visit, daemon=True).start()

    with pytest.raises(GoogleOAuthError, match="state mismatch"):
        authorize_interactively(
            client_id="CLIENT_ID",
            client_secret="CLIENT_SECRET",
            port=8770,
            open_browser=False,
            on_url=simulate_browser,
            transport=httpx.MockTransport(token_handler),
        )
