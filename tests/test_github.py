from pathlib import Path

import httpx

from alfred.db import Database
from alfred.github import GitHubClient, GitHubNotificationsSync


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
