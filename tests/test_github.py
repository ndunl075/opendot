from pathlib import Path

import httpx
import pytest

from alfred.db import Database
from alfred.github import GitHubActions, GitHubClient, GitHubNotificationsSync
from alfred.policy import ApprovalService, PolicyError


def _notification(thread_id: str, updated_at: str, *, title: str = "Fix flaky test") -> dict:
    return {
        "id": thread_id,
        "updated_at": updated_at,
        "reason": "review_requested",
        "unread": True,
        "subject": {
            "title": title,
            "type": "PullRequest",
            "url": "https://api.github.com/repos/example/alfred/pulls/42",
        },
        "repository": {"full_name": "example/alfred"},
    }


class FakeGitHub:
    def list_notifications(self):
        return [_notification("1", "2026-08-11T12:00:00Z")]


def test_github_sync_stores_only_notification_brief_fields(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    first = GitHubNotificationsSync(database, FakeGitHub()).sync()
    second = GitHubNotificationsSync(database, FakeGitHub()).sync()

    assert (first.received, first.stored, second.stored) == (1, 1, 0)
    with database.connect() as connection:
        row = connection.execute("SELECT content, metadata_json FROM events WHERE source = 'github'").fetchone()
        assert row["content"] == "Fix flaky test"
        assert "review_requested" in row["metadata_json"]
        record = connection.execute(
            "SELECT payload_json, active FROM connector_records WHERE connector = 'github' AND record_id = '1'"
        ).fetchone()
        assert record["active"] == 1
        assert "https://github.com/example/alfred/pull/42" in record["payload_json"]
        assert connection.execute("SELECT last_success_at FROM sync_state WHERE connector = 'github'").fetchone()[0]


def test_github_snapshot_marks_resolved_notification_inactive(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    GitHubNotificationsSync(database, FakeGitHub()).sync()

    class ClearedInbox:
        def list_notifications(self):
            return []

    GitHubNotificationsSync(database, ClearedInbox()).sync()
    with database.connect() as connection:
        active = connection.execute(
            "SELECT active FROM connector_records WHERE connector = 'github' AND record_id = '1'"
        ).fetchone()[0]
    assert active == 0


def test_github_client_uses_the_authenticated_notifications_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/notifications"
        assert request.headers["Authorization"] == "Bearer TOKEN"
        assert request.headers["Accept"] == "application/vnd.github+json"
        assert request.url.params["per_page"] == "50"
        return httpx.Response(200, json=[])

    client = GitHubClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        assert client.list_notifications() == []
    finally:
        client.close()


def test_github_issue_creation_is_previewed_then_approval_gated_and_replayed(tmp_path: Path) -> None:
    class FakeGitHubWrite:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str | None]] = []

        def create_issue(self, *, repository: str, title: str, body: str | None) -> dict:
            self.calls.append((repository, title, body))
            return {"number": 42, "html_url": "https://github.com/example/alfred/issues/42"}

    database = Database(tmp_path / "alfred.db")
    approvals = ApprovalService(database)
    write = FakeGitHubWrite()
    actions = GitHubActions(database, approvals, write)
    proposal = actions.propose_issue(
        actor="nico", repository="example/alfred", title="Add a safe action", body="Please add it."
    )
    assert proposal.preview == {"repository": "example/alfred", "title": "Add a safe action", "body": "Please add it."}
    assert write.calls == []

    issued = approvals.approve(proposal.id, actor="nico")
    first = actions.execute(proposal.id, actor="nico", token=issued.token)
    second = actions.execute(proposal.id, actor="nico", token=issued.token)

    assert first.replayed is False
    assert second.replayed is True
    assert second.issue_number == 42
    assert write.calls == [("example/alfred", "Add a safe action", "Please add it.")]


def test_github_issue_proposal_rejects_unscoped_repository_and_wrong_approval(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    approvals = ApprovalService(database)
    actions = GitHubActions(database, approvals)
    with pytest.raises(ValueError, match="owner/repository"):
        actions.propose_issue(actor="nico", repository="https://github.com/example/alfred", title="No")

    unrelated = approvals.propose(actor="nico", action_type="send_message", preview={})
    issued = approvals.approve(unrelated.id, actor="nico")
    with pytest.raises(PolicyError, match="not for GitHub"):
        actions.execute(unrelated.id, actor="nico", token=issued.token)
    assert approvals.get(unrelated.id).state == "approved"


def test_github_client_posts_only_title_and_optional_body_to_issue_endpoint() -> None:
    seen: dict[str, object] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer TOKEN"
        seen["path"] = request.url.path
        seen["body"] = request.content.decode("utf-8")
        return httpx.Response(201, json={"number": 3})

    client = GitHubClient("TOKEN", transport=httpx.MockTransport(capture))
    try:
        assert client.create_issue(repository="example/alfred", title="Issue", body=None)["number"] == 3
    finally:
        client.close()
    assert seen == {"path": "/repos/example/alfred/issues", "body": '{"title":"Issue"}'}


def test_pr_comment_is_approval_gated(tmp_path: Path) -> None:
    class Fake:
        def __init__(self): self.calls = []
        def create_pr_comment(self, **kwargs): self.calls.append(kwargs); return {"id": 9, "html_url": "https://github.com/example/alfred/pull/2#issuecomment-9"}
    database = Database(tmp_path / "alfred.db"); approvals = ApprovalService(database); fake = Fake()
    actions = GitHubActions(database, approvals, fake)
    proposal = actions.propose_pr_comment(actor="nico", repository="example/alfred", pull_number=2, body="Looks good.")
    assert fake.calls == []
    issued = approvals.approve(proposal.id, actor="nico")
    assert actions.execute_pr_comment(proposal.id, actor="nico", token=issued.token).issue_number == 9
    assert fake.calls == [{"repository": "example/alfred", "pull_number": 2, "body": "Looks good."}]
