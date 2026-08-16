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

    def find_issue_by_marker(self, *, repository: str, marker: str) -> dict[str, Any] | None: ...

    def create_pr_comment(self, *, repository: str, pull_number: int, body: str) -> dict[str, Any]: ...

    def find_pr_comment_by_marker(self, *, repository: str, pull_number: int, marker: str) -> dict[str, Any] | None: ...


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

    def find_issue_by_marker(self, *, repository: str, marker: str) -> dict[str, Any] | None:
        """Find one issue bearing Alfred's exact hidden recovery marker."""
        owner, repo = _repository_parts(repository)
        response = self._client.get(
            "/search/issues",
            params={"q": f"repo:{owner}/{repo} type:issue in:body {marker}", "per_page": 2},
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        if not isinstance(items, list) or len(items) > 1:
            raise ValueError("GitHub returned an ambiguous issue recovery result")
        if not items:
            return None
        item = items[0]
        body = item.get("body") if isinstance(item, dict) else None
        if not isinstance(body, str) or marker not in body:
            raise ValueError("GitHub issue recovery result did not contain its marker")
        return item

    def create_pr_comment(self, *, repository: str, pull_number: int, body: str) -> dict[str, Any]:
        owner, repo = _repository_parts(repository)
        response = self._client.post(f"/repos/{owner}/{repo}/issues/{pull_number}/comments", json={"body": body})
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("GitHub PR comment response must be an object")
        return result

    def find_pr_comment_by_marker(self, *, repository: str, pull_number: int, marker: str) -> dict[str, Any] | None:
        owner, repo = _repository_parts(repository)
        comments = self._get_paginated(f"/repos/{owner}/{repo}/issues/{pull_number}/comments", {"per_page": 100})
        matches = [comment for comment in comments if isinstance(comment.get("body"), str) and marker in comment["body"]]
        if len(matches) > 1:
            raise ValueError("GitHub returned an ambiguous PR-comment recovery result")
        return matches[0] if matches else None

    def search_issues(self, query: str, *, per_page: int = 100) -> list[dict[str, Any]]:
        """Run one GitHub issue/PR search query and return matching items."""
        items: list[dict[str, Any]] = []
        page = 1
        for _ in range(10):
            response = self._client.get(
                "/search/issues",
                params={
                    "q": query,
                    "per_page": per_page,
                    "page": page,
                    "sort": "updated",
                    "order": "desc",
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("GitHub search response must be an object")
            batch = payload.get("items", [])
            if not isinstance(batch, list):
                raise ValueError("GitHub search items must be a list")
            items.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < per_page:
                return items
            page += 1
        raise ValueError("GitHub search pagination exceeded 10 pages")

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
        """Create one issue, recovering a hidden-marked issue after a crash."""
        self.database.migrate()
        idempotency_key = f"{self.action_type}:{approval_id}"
        approval = self.approvals.verify(approval_id, actor=actor, token=token)
        if approval.action_type != self.action_type:
            raise PolicyError("approval is not for GitHub issue creation")
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

        if self.transport is None:
            raise ValueError("execute() requires a transport to reach GitHub")
        preview = approval.preview
        marker = _issue_marker(approval_id)
        if approval.state == "consumed":
            created = self.transport.find_issue_by_marker(repository=preview["repository"], marker=marker)
            if created is None:
                raise RuntimeError(
                    "GitHub issue outcome is unknown after a consumed approval; create a new preview instead of retrying"
                )
        else:
            self.approvals.consume(approval_id, actor=actor, token=token)
            body = _append_marker(preview.get("body"), marker)
            created = self.transport.create_issue(repository=preview["repository"], title=preview["title"], body=body)
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
        self.database.migrate()
        key = f"{self.pr_comment_action_type}:{approval_id}"
        approval = self.approvals.verify(approval_id, actor=actor, token=token)
        if approval.action_type != self.pr_comment_action_type:
            raise PolicyError("approval is not for GitHub PR comment")
        with self.database.connect() as connection:
            existing = connection.execute("SELECT actor, payload_json FROM action_receipts WHERE idempotency_key = ?", (key,)).fetchone()
        if existing:
            self.approvals.verify(approval_id, actor=actor, token=token)
            if existing["actor"] != actor: raise PolicyError("approval actor does not match the requested action")
            data = json.loads(existing["payload_json"])
            return GitHubIssueReceipt(issue_number=data["comment_id"], html_url=data.get("html_url"), idempotency_key=key, replayed=True)
        if self.transport is None: raise ValueError("execute_pr_comment() requires a transport")
        preview = approval.preview
        marker = _pr_comment_marker(approval_id)
        if approval.state == "consumed":
            created = self.transport.find_pr_comment_by_marker(
                repository=preview["repository"], pull_number=preview["pull_number"], marker=marker
            )
            if created is None:
                raise RuntimeError(
                    "GitHub PR-comment outcome is unknown after a consumed approval; create a new preview instead of retrying"
                )
        else:
            self.approvals.consume(approval_id, actor=actor, token=token)
            created = self.transport.create_pr_comment(
                repository=preview["repository"], pull_number=preview["pull_number"], body=_append_marker(preview["body"], marker)
            )
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


def _issue_marker(approval_id: str) -> str:
    """Return an invisible, exact body marker for issue recovery."""
    return f"<!-- alfred-action:{approval_id} -->"


def _append_marker(body: str | None, marker: str) -> str:
    return f"{body}\n\n{marker}" if body else marker


def _pr_comment_marker(approval_id: str) -> str:
    return f"<!-- alfred-pr-comment:{approval_id} -->"
