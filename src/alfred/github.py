"""Read-only GitHub notifications sync: issues, PRs, reviews, and mentions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .connector_records import ConnectorRecordStore
from .db import Database
from .events import EventStore


class GitHubTransport(Protocol):
    def list_notifications(self) -> list[dict[str, Any]]: ...


class GitHubClient:
    """Client for the authenticated user's unread notifications feed.

    Notifications cover issue/PR mentions, assignments, and review requests
    across every repository the token can see, so no separate per-repo issue
    or pull-request listing is needed for a single owner's inbox.
    """

    api_base = "https://api.github.com"

    def __init__(
        self, access_token: str, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not access_token.strip():
            raise ValueError("GitHub access token must not be empty")
        self._client = httpx.Client(
            base_url=self.api_base,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=httpx.Timeout(30.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def list_notifications(self) -> list[dict[str, Any]]:
        return self._get_paginated("/notifications", {"per_page": 50})

    def _get_paginated(self, path: str, params: dict[str, Any] | None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = path
        for _ in range(100):
            if next_url is None:
                return items
            response = self._client.get(next_url, params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("GitHub response must be a list")
            items.extend(item for item in payload if isinstance(item, dict))
            next_url = response.links.get("next", {}).get("url")
            params = None
        raise ValueError("GitHub pagination exceeded 100 pages")


class GitHubSyncResult(BaseModel):
    received: int
    stored: int


class GitHubNotificationsSync:
    """Snapshot the current unread-notification inbox; no notification body is kept."""

    connector_name = "github"
    account_name = "self"

    def __init__(self, database: Database, transport: GitHubTransport) -> None:
        self.database = database
        self.transport = transport

    def sync(self) -> GitHubSyncResult:
        self.database.migrate()
        try:
            notifications = self.transport.list_notifications()
        except Exception as error:
            self._store_error(error.__class__.__name__)
            raise
        stored = 0
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                current_records: dict[str, dict[str, Any]] = {}
                for item in notifications:
                    event = _normalize_notification(item)
                    if EventStore.append(connection, **event).is_new:
                        stored += 1
                    metadata = event["metadata"]
                    current_records[metadata["thread_id"]] = {
                        "title": event["content"],
                        "repo": metadata["repo"],
                        "reason": metadata["reason"],
                        "subject_type": metadata["subject_type"],
                        "updated_at": metadata["updated_at"],
                        "html_url": metadata["html_url"],
                    }
                ConnectorRecordStore.replace_snapshot(
                    connection,
                    connector=self.connector_name,
                    account=self.account_name,
                    record_type="notification",
                    records=current_records,
                )
                self._store_success_in_transaction(connection)
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="system:github",
                        client="github",
                        tool="github_read_sync",
                        outcome="ok",
                        result={"received": len(notifications), "stored": stored},
                    ),
                )
        return GitHubSyncResult(received=len(notifications), stored=stored)

    def _store_success_in_transaction(self, connection: Any) -> None:
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at)
            VALUES (?, ?, NULL, ?, NULL, ?)
            ON CONFLICT(connector, account) DO UPDATE SET
                last_success_at = excluded.last_success_at, last_error = NULL, updated_at = excluded.updated_at
            """,
            (self.connector_name, self.account_name, now, now),
        )

    def _store_error(self, reason: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at)
                    VALUES (?, ?, NULL, NULL, ?, ?)
                    ON CONFLICT(connector, account) DO UPDATE SET last_error = excluded.last_error, updated_at = excluded.updated_at
                    """,
                    (self.connector_name, self.account_name, reason, now),
                )


def _normalize_notification(item: dict[str, Any]) -> dict[str, Any]:
    thread_id = item.get("id")
    updated_at = item.get("updated_at")
    if not isinstance(thread_id, str) or not thread_id or not isinstance(updated_at, str):
        raise ValueError("GitHub notification is missing id or updated_at")
    occurred_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    subject_raw = item.get("subject")
    subject = subject_raw if isinstance(subject_raw, dict) else {}
    title = subject.get("title")
    if not isinstance(title, str) or not title:
        title = "Untitled GitHub notification"
    repository_raw = item.get("repository")
    repository = repository_raw if isinstance(repository_raw, dict) else {}
    repo_full_name = repository.get("full_name")
    subject_type = subject.get("type")
    reason = item.get("reason")
    return {
        "source": "github",
        # Versioning makes source events immutable while allowing notification state changes.
        "external_id": f"{thread_id}:{updated_at}",
        "occurred_at": occurred_at,
        "content": title,
        "metadata": {
            "thread_id": thread_id,
            "repo": repo_full_name if isinstance(repo_full_name, str) else None,
            "reason": reason if isinstance(reason, str) else None,
            "subject_type": subject_type if isinstance(subject_type, str) else None,
            "updated_at": updated_at,
            "html_url": _subject_html_url(subject.get("url")),
        },
        "sensitivity": "personal",
    }


def _subject_html_url(api_url: object) -> str | None:
    """Map the API subject URL to its browser deep link for the shapes we recognize."""
    if not isinstance(api_url, str) or not api_url:
        return None
    prefix = "https://api.github.com/repos/"
    if not api_url.startswith(prefix):
        return None
    remainder = api_url[len(prefix) :].replace("/pulls/", "/pull/", 1)
    return f"https://github.com/{remainder}"
