from pathlib import Path

import httpx

from alfred.db import Database
from alfred.gmail import GmailClient, GmailSync


def _message(message_id: str, internal_date: str, *, subject: str = "Re: capstone review") -> dict:
    return {
        "id": message_id,
        "internalDate": internal_date,
        "snippet": "Quick question about the milestone due next week...",
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": "advisor@school.example"},
            ]
        },
    }


class FakeGmail:
    def list_unread_inbox(self):
        return [_message("1", "1786190400000")]


def test_gmail_sync_stores_only_headers_and_snippet(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    first = GmailSync(database, FakeGmail()).sync()
    second = GmailSync(database, FakeGmail()).sync()

    assert (first.received, first.stored, second.stored) == (1, 1, 0)
    with database.connect() as connection:
        row = connection.execute("SELECT content, metadata_json FROM events WHERE source = 'gmail'").fetchone()
        assert row["content"] == "Re: capstone review"
        assert "advisor@school.example" in row["metadata_json"]
        assert "milestone" in row["metadata_json"]
        record = connection.execute(
            "SELECT payload_json, active FROM connector_records WHERE connector = 'gmail' AND record_id = '1'"
        ).fetchone()
        assert record["active"] == 1
        assert "https://mail.google.com/mail/u/0/#inbox/1" in record["payload_json"]
        assert connection.execute("SELECT last_success_at FROM sync_state WHERE connector = 'gmail'").fetchone()[0]


def test_gmail_snapshot_marks_read_message_inactive(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    GmailSync(database, FakeGmail()).sync()

    class ClearedInbox:
        def list_unread_inbox(self):
            return []

    GmailSync(database, ClearedInbox()).sync()
    with database.connect() as connection:
        active = connection.execute(
            "SELECT active FROM connector_records WHERE connector = 'gmail' AND record_id = '1'"
        ).fetchone()[0]
    assert active == 0


def test_gmail_client_lists_then_fetches_metadata_only() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer TOKEN"
        if request.url.path == "/gmail/v1/users/me/messages":
            assert request.url.params["q"] == "is:unread in:inbox"
            return httpx.Response(200, json={"messages": [{"id": "1"}]})
        assert request.url.path == "/gmail/v1/users/me/messages/1"
        assert request.url.params["format"] == "metadata"
        return httpx.Response(200, json=_message("1", "1786190400000"))

    client = GmailClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        messages = client.list_unread_inbox()
    finally:
        client.close()
    assert [message["id"] for message in messages] == ["1"]
    assert calls == ["/gmail/v1/users/me/messages", "/gmail/v1/users/me/messages/1"]
