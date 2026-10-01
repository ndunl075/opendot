"""One sync of a connected app, for the UI's "Sync now" (M4 task 4.4).

Each function mints credentials from the given secret store (the OS keychain in the daemon) and runs
the same sync the `opendot gmail-sync` / `calendar-sync` / `github-sync` commands run. Reads only:
nothing here writes to an outside app.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from .db import Database
from .github import GitHubClient, GitHubNotificationsSync
from .gmail import GmailClient, GmailSync
from .google_calendar import GoogleCalendarClient, GoogleCalendarSync, default_sync_window
from .google_oauth import current_access_token
from .secret_store import SecretStore

GITHUB_TOKEN_SECRET = "github-issue-token"
CALENDAR_DAYS = 14


def _require(database: Database, app: str) -> None:
    from .connections_google import GoogleConnectError, google_app_connected, google_managed

    if google_managed(database) and not google_app_connected(database, app):
        raise GoogleConnectError(f"{app} is not connected.")


def sync_gmail(database: Database, secrets: SecretStore) -> Any:
    _require(database, "gmail")
    client = GmailClient(current_access_token(secrets))
    try:
        return GmailSync(database, client).sync()
    finally:
        client.close()


def sync_calendar(database: Database, secrets: SecretStore) -> Any:
    _require(database, "google_calendar")
    start, _ = default_sync_window()
    client = GoogleCalendarClient(current_access_token(secrets))
    try:
        return GoogleCalendarSync(database, client).sync(
            calendar_id="primary", time_min=start, time_max=start + timedelta(days=CALENDAR_DAYS)
        )
    finally:
        client.close()


def sync_github(database: Database, secrets: SecretStore) -> Any:
    client = GitHubClient(secrets.get_required(GITHUB_TOKEN_SECRET))
    try:
        return GitHubNotificationsSync(database, client).sync()
    finally:
        client.close()


def pull_request_report(secrets: SecretStore | None = None) -> Callable[[], Any] | None:
    """For the weekly review: the open pull requests from GitHub (read-only), or None when no GitHub
    token is saved. Errors propagate; the review leaves the section out when this fails."""
    from .pull_requests import PullRequestService
    from .secret_store import SecretStoreError, SystemKeyringSecretStore

    store = secrets or SystemKeyringSecretStore()
    try:
        store.get_required(GITHUB_TOKEN_SECRET)
    except SecretStoreError:
        return None

    def report() -> Any:
        client = GitHubClient(store.get_required(GITHUB_TOKEN_SECRET))
        try:
            return PullRequestService(client).get()
        finally:
            client.close()

    return report


def build_syncers(database: Database, secrets: SecretStore) -> dict[str, Callable[[], Any]]:
    """``{connection id: run one sync}`` for ``ApiContext.extras["connector_syncers"]``."""
    return {
        "gmail": lambda: sync_gmail(database, secrets),
        "google_calendar": lambda: sync_calendar(database, secrets),
        "github": lambda: sync_github(database, secrets),
    }


__all__ = ["build_syncers", "pull_request_report", "sync_calendar", "sync_github", "sync_gmail"]
