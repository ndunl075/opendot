"""Connecting Google (Gmail and Calendar) from the UI with the user's own OAuth client (M4 task 4.4).

- The user brings their own Google OAuth client ("Desktop app" type, created in their own Google
  Cloud project, guide in docs/google-oauth-setup.md). Its id and secret, and the refresh token
  Google returns, live only in the OS keychain.
- Read-only by default (``google_oauth.READ_SCOPES``). Switching on the write opt-in (Gmail drafts,
  Calendar events) asks Google for ``WRITE_SCOPES`` too, as an incremental grant; until Google has
  granted them, the agent is not offered the draft and event tools.
- The redirect comes back to the daemon itself (``/oauth/google/callback`` on 127.0.0.1), with a
  one-time ``state`` and PKCE. Pending sign-ins live in memory and expire after 10 minutes.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .db import Database
from .google_oauth import READ_SCOPES, WRITE_SCOPES, GoogleOAuthClient, build_authorization_url, pkce_pair
from .secret_store import SecretStore, SecretStoreError
from .settings_store import SettingsStore

CLIENT_ID_SECRET = "google-oauth-client-id"
CLIENT_SECRET_SECRET = "google-oauth-client-secret"
REFRESH_TOKEN_SECRET = "google-oauth-refresh-token"
SETTINGS_KEY = "google_connection"
CALLBACK_PATH = "/oauth/google/callback"
PENDING_TTL_SECONDS = 600.0
GOOGLE_APPS = ("gmail", "google_calendar")


def google_write_allowed(database: Database) -> bool:
    """The user switched Google writes on AND Google granted the write scopes."""
    state = SettingsStore(database).get(SETTINGS_KEY, dict, {})
    granted = set(state.get("granted_scopes") or [])
    return bool(state.get("write_opt_in")) and set(WRITE_SCOPES) <= granted


class GoogleConnectError(ValueError):
    """Something the user can fix (shown as-is)."""


@dataclass
class _Pending:
    verifier: str
    scopes: tuple[str, ...]
    redirect_uri: str
    created: float


@dataclass
class GoogleConnector:
    database: Database
    secrets: SecretStore
    base_url: str = "http://127.0.0.1:8765"
    transport: httpx.BaseTransport | None = None
    clock: Any = time.monotonic
    _pending: dict[str, _Pending] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- the user's OAuth client ---------------------------------------------------------------

    def client_configured(self) -> bool:
        try:
            return bool(self.secrets.get_required(CLIENT_ID_SECRET) and self.secrets.get_required(CLIENT_SECRET_SECRET))
        except SecretStoreError:
            return False

    def save_client(self, client_id: str, client_secret: str) -> None:
        client_id, client_secret = client_id.strip(), client_secret.strip()
        if not client_id.endswith(".apps.googleusercontent.com"):
            raise GoogleConnectError("That is not a Google OAuth client ID (it ends in .apps.googleusercontent.com).")
        if len(client_secret) < 10 or any(ch.isspace() for ch in client_secret):
            raise GoogleConnectError("That client secret does not look right. Copy it again from Google Cloud.")
        self.secrets.store(CLIENT_ID_SECRET, client_id)
        self.secrets.store(CLIENT_SECRET_SECRET, client_secret)

    # -- state ------------------------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        return SettingsStore(self.database).get(SETTINGS_KEY, dict, {})

    def _save_state(self, **changes: Any) -> dict[str, Any]:
        current = self.state()
        current.update(changes)
        SettingsStore(self.database).set(SETTINGS_KEY, current)
        return current

    def connected(self) -> bool:
        try:
            return bool(self.secrets.get_required(REFRESH_TOKEN_SECRET))
        except SecretStoreError:
            return False

    def write_allowed(self) -> bool:
        return google_write_allowed(self.database)

    # -- the sign-in flow ------------------------------------------------------------------------

    def start(self, *, include_write: bool | None = None) -> tuple[str, str]:
        """Return (authorize_url, state). Opens nothing: the UI or desktop shell opens the browser."""
        if not self.client_configured():
            raise GoogleConnectError("Add your Google OAuth client first (Connections, Set up Google).")
        if include_write is None:
            include_write = bool(self.state().get("write_opt_in"))
        scopes = READ_SCOPES + (WRITE_SCOPES if include_write else ())
        state = secrets.token_urlsafe(24)
        verifier, challenge = pkce_pair()
        redirect_uri = f"{self.base_url}{CALLBACK_PATH}"
        with self._lock:
            self._expire()
            self._pending[state] = _Pending(verifier, scopes, redirect_uri, self.clock())
        url = build_authorization_url(
            client_id=self.secrets.get_required(CLIENT_ID_SECRET),
            redirect_uri=redirect_uri,
            scopes=scopes,
            state=state,
            code_challenge=challenge,
        )
        return url, state

    def complete(self, *, state: str | None, code: str | None, error: str | None = None) -> dict[str, Any]:
        """Finish the sign-in from Google's redirect. The state is single-use."""
        with self._lock:
            self._expire()
            pending = self._pending.pop(state or "", None)
        if pending is None:
            raise GoogleConnectError("This sign-in link has expired or was already used. Start again from OpenDot.")
        if error or not code:
            raise GoogleConnectError(f"Google did not grant access ({error or 'no code'}).")
        client = GoogleOAuthClient(
            self.secrets.get_required(CLIENT_ID_SECRET),
            self.secrets.get_required(CLIENT_SECRET_SECRET),
            transport=self.transport,
        )
        try:
            token = client.exchange_code(code, redirect_uri=pending.redirect_uri, code_verifier=pending.verifier)
        finally:
            client.close()
        if token.refresh_token:
            self.secrets.store(REFRESH_TOKEN_SECRET, token.refresh_token)
        elif not self.connected():
            raise GoogleConnectError(
                "Google did not return a refresh token. Remove OpenDot at myaccount.google.com/permissions and try again."
            )
        granted = sorted(set((token.scope or "").split()) | set(self.state().get("granted_scopes") or []))
        return self._save_state(granted_scopes=granted, connected=True)

    def set_write_opt_in(self, enabled: bool) -> tuple[dict[str, Any], str | None]:
        """Switch writes on or off. Turning on without the write grant yet returns the URL to get it."""
        state = self._save_state(write_opt_in=bool(enabled))
        if enabled and not self.write_allowed():
            url, _ = self.start(include_write=True)
            return state, url
        return state, None

    def disconnect(self) -> None:
        try:
            self.secrets.delete(REFRESH_TOKEN_SECRET)
        except (SecretStoreError, AttributeError):
            pass
        self._save_state(connected=False, granted_scopes=[], write_opt_in=False)

    def _expire(self) -> None:
        now = self.clock()
        for key in [k for k, p in self._pending.items() if now - p.created > PENDING_TTL_SECONDS]:
            del self._pending[key]


__all__ = ["CALLBACK_PATH", "GOOGLE_APPS", "GoogleConnectError", "GoogleConnector", "google_write_allowed"]
