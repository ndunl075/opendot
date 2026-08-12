import base64
import json
from pathlib import Path

import httpx
import pytest

from alfred.db import Database
from alfred.gmail import GmailActions, GmailClient, GmailSendActions, GmailSync
from alfred.policy import ApprovalService, PolicyError


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


def test_gmail_client_create_draft_sends_a_valid_rfc2822_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/gmail/v1/users/me/drafts"
        payload = json.loads(request.read())
        raw = base64.urlsafe_b64decode(payload["message"]["raw"]).decode("utf-8")
        assert "To: advisor@school.example" in raw
        assert "Subject: Question" in raw
        assert "Quick question about the deadline." in raw
        return httpx.Response(200, json={"id": "draft1"})

    client = GmailClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        created = client.create_draft(to="advisor@school.example", subject="Question", body="Quick question about the deadline.")
    finally:
        client.close()
    assert created["id"] == "draft1"


def test_gmail_client_send_uses_the_messages_send_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/gmail/v1/users/me/messages/send"
        raw = base64.urlsafe_b64decode(json.loads(request.read())["raw"]).decode("utf-8")
        assert "To: advisor@school.example" in raw
        assert "Subject: Approved update" in raw
        return httpx.Response(200, json={"id": "sent1"})

    client = GmailClient("TOKEN", transport=httpx.MockTransport(handler))
    try:
        assert client.send_message(to="advisor@school.example", subject="Approved update", body="Thanks.")["id"] == "sent1"
    finally:
        client.close()


class FakeDraftTransport:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create_draft(self, *, to, subject, body):
        self.calls.append({"to": to, "subject": subject, "body": body})
        return {"id": f"draft-{len(self.calls)}"}


class FakeSendTransport:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_message(self, *, to, subject, body):
        self.calls.append({"to": to, "subject": subject, "body": body})
        return {"id": f"sent-{len(self.calls)}"}


def test_gmail_draft_is_never_created_without_a_consumed_approval(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    approvals = ApprovalService(database)
    transport = FakeDraftTransport()
    actions = GmailActions(database, approvals)

    proposed = actions.propose_draft(actor="nico", to="advisor@school.example", subject="Question", body="Quick question.")
    assert transport.calls == []  # proposing alone must never touch Gmail

    with pytest.raises(PolicyError, match="not ready to consume"):
        GmailActions(database, approvals, transport).execute(proposed.id, actor="nico", token="not-a-real-token")
    assert transport.calls == []

    issued = approvals.approve(proposed.id, actor="nico")
    receipt = GmailActions(database, approvals, transport).execute(proposed.id, actor="nico", token=issued.token)

    assert receipt.replayed is False
    assert receipt.draft_id == "draft-1"
    assert transport.calls == [{"to": "advisor@school.example", "subject": "Question", "body": "Quick question."}]


def test_gmail_execute_replays_the_receipt_instead_of_creating_twice(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    approvals = ApprovalService(database)
    transport = FakeDraftTransport()
    proposed = GmailActions(database, approvals).propose_draft(
        actor="nico", to="advisor@school.example", subject="Question", body="Quick question."
    )
    issued = approvals.approve(proposed.id, actor="nico")

    first = GmailActions(database, approvals, transport).execute(proposed.id, actor="nico", token=issued.token)
    second = GmailActions(database, approvals, transport).execute(proposed.id, actor="nico", token=issued.token)

    assert first.replayed is False
    assert second.replayed is True
    assert second.draft_id == first.draft_id
    assert len(transport.calls) == 1  # Gmail was only ever asked to create the draft once

    with pytest.raises(PolicyError, match="does not match"):
        GmailActions(database, approvals, transport).execute(proposed.id, actor="someone-else", token=issued.token)
    with pytest.raises(PolicyError, match="invalid"):
        GmailActions(database, approvals, transport).execute(proposed.id, actor="nico", token="wrong-token")


def test_gmail_send_is_approval_gated_and_replayed_once(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    approvals = ApprovalService(database)
    transport = FakeSendTransport()
    proposed = GmailSendActions(database, approvals).propose_send(
        actor="nico", to="advisor@school.example", subject="Update", body="Approved body."
    )
    assert transport.calls == []
    issued = approvals.approve(proposed.id, actor="nico")
    first = GmailSendActions(database, approvals, transport).execute(proposed.id, actor="nico", token=issued.token)
    second = GmailSendActions(database, approvals, transport).execute(proposed.id, actor="nico", token=issued.token)
    assert first.message_id == "sent-1"
    assert second.replayed is True
    assert len(transport.calls) == 1
