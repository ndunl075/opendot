"""Read-only GitHub notifications sync: issues, PRs, reviews, and mentions."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .connector_records import ConnectorRecordStore
from .db import Database
from .events import EventStore
from .policy import Approval, ApprovalService, PolicyError


class GitHubTransport(Protocol):
    def list_notifications(self) -> list[dict[str, Any]]: ...


class GitHubIssueWriteTransport(Protocol):
    def create_issue(self, *, repository: str, title: str, body: str | None) -> dict[str, Any]: ...

    def create_pr_comment(self, *, repository: str, pull_number: int, body: str) -> dict[str, Any]: ...


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

    def create_issue(self, *, repository: str, title: str, body: str | None) -> dict[str, Any]:
        """Create one issue in an explicitly named repository.

        This narrow primitive performs no approval work itself. ``GitHubActions``
        owns the preview, human confirmation, and local idempotency receipt.
        """
        owner, repo = _repository_parts(repository)
        payload: dict[str, str] = {"title": title}
        if body:
            payload["body"] = body
        response = self._client.post(f"/repos/{owner}/{repo}/issues", json=payload)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("GitHub issue-create response must be an object")
        return result

    def create_pr_comment(self, *, repository: str, pull_number: int, body: str) -> dict[str, Any]:
        owner, repo = _repository_parts(repository)
        response = self._client.post(f"/repos/{owner}/{repo}/issues/{pull_number}/comments", json={"body": body})
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("GitHub PR comment response must be an object")
        return result

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


class GitHubIssueReceipt(BaseModel):
    issue_number: int
    html_url: str | None
    idempotency_key: str
    replayed: bool


class GitHubActions:
    """Approval-gated GitHub issue creation for an explicitly scoped repository."""

    connector_name = "github"
    action_type = "github_issue_create"
    pr_comment_action_type = "github_pr_comment_create"

    def __init__(
        self, database: Database, approvals: ApprovalService, transport: GitHubIssueWriteTransport | None = None
    ) -> None:
        self.database = database
        self.approvals = approvals
        self.transport = transport

    def propose_issue(self, *, actor: str, repository: str, title: str, body: str | None = None) -> Approval:
        """Validate and preview an issue; no GitHub credential is read here."""
        _repository_parts(repository)
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("GitHub issue title cannot be empty")
        if len(normalized_title) > 256:
            raise ValueError("GitHub issue title must be 256 characters or fewer")
        normalized_body = body.strip() if body else None
        return self.approvals.propose(
            actor=actor,
            action_type=self.action_type,
            preview={"repository": repository, "title": normalized_title, "body": normalized_body},
        )

    def execute(self, approval_id: str, *, actor: str, token: str) -> GitHubIssueReceipt:
        """Create the approved issue once locally, then retain its receipt for replay."""
        self.database.migrate()
        idempotency_key = f"{self.action_type}:{approval_id}"
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT actor, payload_json FROM action_receipts WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        if existing is not None:
            self.approvals.verify(approval_id, actor=actor, token=token)
            if existing["actor"] != actor:
                raise PolicyError("approval actor does not match the requested action")
            payload = json.loads(existing["payload_json"])
            return GitHubIssueReceipt(
                issue_number=payload["issue_number"],
                html_url=payload.get("html_url"),
                idempotency_key=idempotency_key,
                replayed=True,
            )

        approval = self.approvals.get(approval_id)
        if approval is None or approval.action_type != self.action_type:
            raise PolicyError("approval is not for GitHub issue creation")
        if self.transport is None:
            raise ValueError("execute() requires a transport to reach GitHub")
        consumed = self.approvals.consume(approval_id, actor=actor, token=token)
        preview = consumed.preview
        created = self.transport.create_issue(
            repository=preview["repository"], title=preview["title"], body=preview.get("body")
        )
        issue_number = created.get("number")
        if not isinstance(issue_number, int) or issue_number <= 0:
            raise ValueError("GitHub did not return a valid issue number")
        html_url = created.get("html_url")
        if html_url is not None and not isinstance(html_url, str):
            raise ValueError("GitHub returned an invalid issue URL")
        payload = {"issue_number": issue_number, "html_url": html_url}
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    INSERT INTO action_receipts (idempotency_key, connector, action_type, approval_id, actor, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(idempotency_key) DO NOTHING
                    """,
                    (
                        idempotency_key,
                        self.connector_name,
                        self.action_type,
                        approval_id,
                        actor,
                        json.dumps(payload, sort_keys=True),
                        datetime.now(UTC).isoformat(),
                    ),
                )
        return GitHubIssueReceipt(
            issue_number=issue_number, html_url=html_url, idempotency_key=idempotency_key, replayed=False
        )

    def propose_pr_comment(self, *, actor: str, repository: str, pull_number: int, body: str) -> Approval:
        _repository_parts(repository)
        if pull_number <= 0 or not body.strip():
            raise ValueError("pull number must be positive and comment body must not be empty")
        return self.approvals.propose(actor=actor, action_type=self.pr_comment_action_type,
            preview={"repository": repository, "pull_number": pull_number, "body": body})

    def execute_pr_comment(self, approval_id: str, *, actor: str, token: str) -> GitHubIssueReceipt:
        key = f"{self.pr_comment_action_type}:{approval_id}"
        with self.database.connect() as connection:
            existing = connection.execute("SELECT actor, payload_json FROM action_receipts WHERE idempotency_key = ?", (key,)).fetchone()
        if existing:
            self.approvals.verify(approval_id, actor=actor, token=token)
            if existing["actor"] != actor: raise PolicyError("approval actor does not match the requested action")
            data = json.loads(existing["payload_json"])
            return GitHubIssueReceipt(issue_number=data["comment_id"], html_url=data.get("html_url"), idempotency_key=key, replayed=True)
        approval = self.approvals.get(approval_id)
        if approval is None or approval.action_type != self.pr_comment_action_type: raise PolicyError("approval is not for GitHub PR comment")
        if self.transport is None: raise ValueError("execute_pr_comment() requires a transport")
        created = self.transport.create_pr_comment(**self.approvals.consume(approval_id, actor=actor, token=token).preview)
        comment_id = created.get("id")
        if not isinstance(comment_id, int) or comment_id <= 0: raise ValueError("GitHub did not return a valid comment id")
        data = {"comment_id": comment_id, "html_url": created.get("html_url")}
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute("INSERT INTO action_receipts (idempotency_key, connector, action_type, approval_id, actor, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (key, self.connector_name, self.pr_comment_action_type, approval_id, actor, json.dumps(data, sort_keys=True), datetime.now(UTC).isoformat()))
        return GitHubIssueReceipt(issue_number=comment_id, html_url=data["html_url"], idempotency_key=key, replayed=False)

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


def _repository_parts(repository: str) -> tuple[str, str]:
    """Validate owner/repository before it becomes an API path."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be an explicit owner/repository name")
    return tuple(repository.split("/", maxsplit=1))  # type: ignore[return-value]
