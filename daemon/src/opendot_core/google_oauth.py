"""Google OAuth 2.0 for a private installed app: authorize once, refresh after.

Follows RFC 8252 (OAuth for native apps): a local loopback HTTP listener
receives the authorization redirect, so no client secret ever needs to be
embedded in a public redirect URI and no port needs pre-registration with
Google for a "Desktop app" OAuth client.
"""

from __future__ import annotations

import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from pydantic import BaseModel

from .secret_store import SecretStore

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# calendar.events covers the read sync and the approval-gated event write;
# gmail.readonly covers the unread-inbox sync; gmail.compose covers the
# approval-gated draft write. gmail.compose's own grant may be broader than
# what Alfred exercises -- the real boundary is that GmailClient's code
# never calls a send endpoint, only drafts.create, regardless of what the
# token could technically do. All three scopes come from one consent
# screen, so Calendar and Gmail share a single refresh token.
DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    # Required to discover the subscribed calendars whose events are merged
    # in Google Calendar's UI. Without it, "primary is clear" can be mistaken
    # for "your calendar is clear" while shared/selected calendars are absent.
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
)


class GoogleOAuthError(RuntimeError):
    """Raised when the local OAuth flow or a token exchange fails."""


class TokenResponse(BaseModel):
    access_token: str
    expires_in: int
    refresh_token: str | None = None
    scope: str | None = None
    token_type: str | None = None


class CallbackResult(BaseModel):
    code: str | None = None
    error: str | None = None
    state: str | None = None


def build_authorization_url(*, client_id: str, redirect_uri: str, scopes: tuple[str, ...], state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        # offline + consent guarantees a refresh_token even on a repeat authorization.
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


class LocalRedirectListener:
    """A single-use local HTTP server that captures Google's OAuth redirect.

    Binds to 127.0.0.1 only and serves exactly one request, then the caller
    is expected to close it -- it never lingers as an open local port.
    """

    def __init__(self, *, port: int) -> None:
        self.port = port
        self._result: CallbackResult | None = None
        self._ready = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return  # silence default per-request stderr logging

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
                query = parse_qs(urlparse(self.path).query)
                outer._result = CallbackResult(
                    code=query.get("code", [None])[0],
                    error=query.get("error", [None])[0],
                    state=query.get("state", [None])[0],
                )
                message = "Alfred received the authorization. You can close this tab." if outer._result.code else "Authorization failed. You can close this tab."
                body = f"<html><body><p>{message}</p></body></html>".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                outer._ready.set()

        self._server = HTTPServer(("127.0.0.1", port), Handler)

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def wait_for_redirect(self, *, timeout_seconds: float = 300.0) -> CallbackResult:
        """Serve exactly one request, blocking until it arrives or the timeout elapses."""
        thread = threading.Thread(target=self._server.handle_request, daemon=True)
        thread.start()
        try:
            if not self._ready.wait(timeout=timeout_seconds):
                raise GoogleOAuthError("timed out waiting for the Google authorization redirect")
        finally:
            self._server.server_close()
            thread.join(timeout=5)
        if self._result is None:
            raise GoogleOAuthError("no redirect was captured")
        return self._result


class GoogleOAuthClient:
    """Token exchange against Google's OAuth endpoint; no browser interaction here."""

    def __init__(self, client_id: str, client_secret: str, *, transport: httpx.BaseTransport | None = None) -> None:
        if not client_id.strip() or not client_secret.strip():
            raise ValueError("Google OAuth client ID and secret must not be empty")
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = httpx.Client(timeout=httpx.Timeout(30.0), transport=transport)

    def close(self) -> None:
        self._client.close()

    def exchange_code(self, code: str, *, redirect_uri: str) -> TokenResponse:
        return self._post(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            }
        )

    def refresh_access_token(
        self, refresh_token: str, *, scopes: tuple[str, ...] = ()
    ) -> TokenResponse:
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        # Google Health rejects access tokens that also carry Calendar/Gmail
        # scopes (DISALLOWED_OAUTH_SCOPES). A refresh can request a subset of
        # the original grant so Health calls mint a health-only token.
        if scopes:
            data["scope"] = " ".join(scopes)
        return self._post(data)

    def _post(self, data: dict[str, str]) -> TokenResponse:
        response = self._client.post(TOKEN_ENDPOINT, data=data)
        if response.status_code >= 400:
            raise GoogleOAuthError(f"Google token request failed ({response.status_code}): {response.text}")
        return TokenResponse.model_validate(response.json())


def authorize_interactively(
    *,
    client_id: str,
    client_secret: str,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
    port: int = 8765,
    timeout_seconds: float = 300.0,
    open_browser: bool = True,
    on_url: Callable[[str], None] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> TokenResponse:
    """Run the one-time interactive consent flow and return the granted tokens.

    ``on_url`` lets the caller display the authorization URL (always useful,
    e.g. over SSH where ``open_browser`` can't do anything); it is also how
    tests simulate the browser without one actually opening.
    """
    listener = LocalRedirectListener(port=port)
    state = secrets.token_urlsafe(16)
    url = build_authorization_url(client_id=client_id, redirect_uri=listener.redirect_uri, scopes=scopes, state=state)
    if on_url is not None:
        on_url(url)
    if open_browser:
        webbrowser.open(url)
    result = listener.wait_for_redirect(timeout_seconds=timeout_seconds)
    if result.error or not result.code:
        raise GoogleOAuthError(f"Google authorization was not granted: {result.error or 'no code returned'}")
    if result.state != state:
        raise GoogleOAuthError("OAuth state mismatch; discarding this redirect")
    oauth_client = GoogleOAuthClient(client_id, client_secret, transport=transport)
    try:
        return oauth_client.exchange_code(result.code, redirect_uri=listener.redirect_uri)
    finally:
        oauth_client.close()


def current_access_token(secret_store: SecretStore, *, scopes: tuple[str, ...] = ()) -> str:
    """Mint a fresh access token from the stored refresh token; nothing is cached locally.

    Shared by every caller that needs a live Google credential -- the CLI's
    connector-sync commands and, now, MCP's action_commit for the calendar
    write -- so there is exactly one place that knows how to do this.
    ``scopes`` requests a subset of the original grant (needed for Google
    Health, which rejects mixed Calendar/Gmail tokens).
    """
    oauth_client = GoogleOAuthClient(
        secret_store.get_required("google-oauth-client-id"),
        secret_store.get_required("google-oauth-client-secret"),
    )
    try:
        return oauth_client.refresh_access_token(
            secret_store.get_required("google-oauth-refresh-token"),
            scopes=scopes,
        ).access_token
    finally:
        oauth_client.close()
