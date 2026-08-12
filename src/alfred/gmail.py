"""Gmail sync (read) plus one narrowly scoped, approval-gated draft write."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from email.mime.text import MIMEText
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .connector_records import ConnectorRecordStore
from .db import Database
from .events import EventStore
from .policy import Approval, ApprovalService, PolicyError


class GmailTransport(Protocol):
    def list_unread_inbox(self) -> list[dict[str, Any]]: ...


class GmailWriteTransport(Protocol):
    def create_draft(self, *, message_id: str, to: str, subject: str, body: str) -> dict[str, Any]: ...

    def find_draft_by_message_id(self, *, message_id: str) -> dict[str, Any] | None: ...

    def send_message(self, *, message_id: str, to: str, subject: str, body: str) -> dict[str, Any]: ...

    def find_sent_message_by_message_id(self, *, message_id: str) -> dict[str, Any] | None: ...


class GmailClient:
    """Client for the unread-inbox message list, fetched at metadata scope only.

    ``format=metadata`` never returns the message body or attachments, matching
    the architecture rule that mail is indexed by ID and a minimal excerpt
    rather than copied wholesale; Gmail's own ``snippet`` field is that excerpt.
    """

    api_base = "https://gmail.googleapis.com/gmail/v1"

    def __init__(
        self, access_token: str, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not access_token.strip():
            raise ValueError("Gmail access token must not be empty")
        self._client = httpx.Client(
            base_url=self.api_base,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=httpx.Timeout(30.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def list_unread_inbox(self) -> list[dict[str, Any]]:
        return [self._get_message(message_id) for message_id in self._list_message_ids()]

    def _list_message_ids(self) -> list[str]:
        # 100 pages of 100 matches the cap CanvasClient._get_paginated already
        # uses in this codebase -- generous enough for a large real unread
        # backlog (up to 10,000 messages) while still a real safety valve
        # against a runaway loop, not an arbitrary number picked in isolation.
        ids: list[str] = []
        params: dict[str, Any] = {"q": "is:unread in:inbox", "maxResults": 100}
        for _ in range(100):
            response = self._client.get("/users/me/messages", params=params)
            response.raise_for_status()
            payload = response.json()
            ids.extend(
                message["id"]
                for message in payload.get("messages", [])
                if isinstance(message, dict) and isinstance(message.get("id"), str)
            )
            page_token = payload.get("nextPageToken")
            if not page_token:
                return ids
            params = {"q": "is:unread in:inbox", "maxResults": 100, "pageToken": page_token}
        raise ValueError("Gmail pagination exceeded 100 pages")

    def _get_message(self, message_id: str) -> dict[str, Any]:
        response = self._client.get(
            f"/users/me/messages/{message_id}",
            params={"format": "metadata", "metadataHeaders": ["Subject", "From"]},
        )
        response.raise_for_status()
        return response.json()

    def create_draft(self, *, message_id: str, to: str, subject: str, body: str) -> dict[str, Any]:
        """Create one Gmail draft with a stable RFC 2822 Message-ID."""
        message = MIMEText(body)
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = message_id
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        response = self._client.post("/users/me/drafts", json={"message": {"raw": raw}})
        response.raise_for_status()
        return response.json()

    def find_draft_by_message_id(self, *, message_id: str) -> dict[str, Any] | None:
        """Find one draft by Message-ID without fetching its content."""
        response = self._client.get("/users/me/messages", params={"q": f"rfc822msgid:{message_id}", "maxResults": 2})
        response.raise_for_status()
        messages = response.json().get("messages", [])
        if not isinstance(messages, list) or len(messages) > 1:
            raise ValueError("Gmail returned an ambiguous Message-ID search result")
        if not messages:
            return None
        found_message_id = messages[0].get("id") if isinstance(messages[0], dict) else None
        if not isinstance(found_message_id, str) or not found_message_id:
            raise ValueError("Gmail returned an invalid Message-ID search result")
        page_token: str | None = None
        for _ in range(20):
            params: dict[str, str | int] = {"maxResults": 500}
            if page_token:
                params["pageToken"] = page_token
            drafts_response = self._client.get("/users/me/drafts", params=params)
            drafts_response.raise_for_status()
            payload = drafts_response.json()
            drafts = payload.get("drafts", [])
            if not isinstance(drafts, list):
                raise ValueError("Gmail returned invalid drafts")
            for draft in drafts:
                nested_message = draft.get("message") if isinstance(draft, dict) else None
                if isinstance(nested_message, dict) and nested_message.get("id") == found_message_id:
                    draft_id = draft.get("id")
                    if isinstance(draft_id, str) and draft_id:
                        return {"id": draft_id}
                    raise ValueError("Gmail returned a draft without an ID")
            next_token = payload.get("nextPageToken")
            if next_token is None:
                return None
            if not isinstance(next_token, str) or not next_token:
                raise ValueError("Gmail returned an invalid drafts page token")
            page_token = next_token
        raise RuntimeError("Gmail draft recovery exceeded the pagination limit")

    def send_message(self, *, message_id: str, to: str, subject: str, body: str) -> dict[str, Any]:
        """Send a message only when called by the separately approval-gated action."""
        message = MIMEText(body)
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = message_id
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        response = self._client.post("/users/me/messages/send", json={"raw": raw})
        response.raise_for_status()
        return response.json()

    def find_sent_message_by_message_id(self, *, message_id: str) -> dict[str, Any] | None:
        response = self._client.get(
            "/users/me/messages", params={"q": f"rfc822msgid:{message_id} in:sent", "maxResults": 2}
        )
        response.raise_for_status()
        messages = response.json().get("messages", [])
        if not isinstance(messages, list) or len(messages) > 1:
            raise ValueError("Gmail returned an ambiguous sent-message recovery result")
        if not messages:
            return None
        found = messages[0]
        message_id_from_search = found.get("id") if isinstance(found, dict) else None
        if not isinstance(message_id_from_search, str) or not message_id_from_search:
            raise ValueError("Gmail returned an invalid sent-message recovery result")
        return {"id": message_id_from_search}


class GmailSyncResult(BaseModel):
    received: int
    stored: int


class GmailSync:
    """Snapshot the current unread inbox; a read or archived message drops out."""

    connector_name = "gmail"
    account_name = "self"

    def __init__(self, database: Database, transport: GmailTransport) -> None:
        self.database = database
        self.transport = transport

    def sync(self) -> GmailSyncResult:
        self.database.migrate()
        try:
            messages = self.transport.list_unread_inbox()
        except Exception as error:
            self._store_error(error.__class__.__name__)
            raise
        stored = 0
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                current_records: dict[str, dict[str, Any]] = {}
                for item in messages:
                    event = _normalize_message(item)
                    if EventStore.append(connection, **event).is_new:
                        stored += 1
                    metadata = event["metadata"]
                    current_records[metadata["message_id"]] = {
                        "subject": event["content"],
                        "from": metadata["from"],
                        "snippet": metadata["snippet"],
                        "html_url": metadata["html_url"],
                    }
                ConnectorRecordStore.replace_snapshot(
                    connection,
                    connector=self.connector_name,
                    account=self.account_name,
                    record_type="unread_message",
                    records=current_records,
                )
                self._store_success_in_transaction(connection)
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="system:gmail",
                        client="gmail",
                        tool="gmail_read_sync",
                        outcome="ok",
                        result={"received": len(messages), "stored": stored},
                    ),
                )
        return GmailSyncResult(received=len(messages), stored=stored)

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


class GmailDraftReceipt(BaseModel):
    draft_id: str
    idempotency_key: str
    replayed: bool


class GmailActions:
    """A narrowly scoped, approval-gated write: create one Gmail draft.

    Matches the connector contract's propose()/execute() shape and section
    7's own language ("message_draft and other consequential operations
    return a preview"): even a draft -- not a sent message -- is treated as
    consequential, so nothing reaches Gmail until a proposal is approved and
    its one-time token is presented to execute(). Sending is handled by the
    separate GmailSendActions flow, never by this draft action.
    """

    connector_name = "gmail"
    action_type = "gmail_draft_create"

    def __init__(
        self, database: Database, approvals: ApprovalService, transport: GmailWriteTransport | None = None
    ) -> None:
        """``transport`` is only required for execute(); propose_draft() never touches Gmail."""
        self.database = database
        self.approvals = approvals
        self.transport = transport

    def propose_draft(self, *, actor: str, to: str, subject: str, body: str) -> Approval:
        """Preview a Gmail draft without sending anything to Gmail yet."""
        preview = {"to": to, "subject": subject, "body": body}
        return self.approvals.propose(actor=actor, action_type=self.action_type, preview=preview)

    def execute(self, approval_id: str, *, actor: str, token: str) -> GmailDraftReceipt:
        """Create a draft once, recovering it by a stable Message-ID after a crash."""
        self.database.migrate()
        idempotency_key = f"{self.action_type}:{approval_id}"
        approval = self.approvals.verify(approval_id, actor=actor, token=token)
        if approval.action_type != self.action_type:
            raise PolicyError("approval is not for a Gmail draft")
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT actor, payload_json FROM action_receipts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if existing is not None:
            self.approvals.verify(approval_id, actor=actor, token=token)
            if existing["actor"] != actor:
                raise PolicyError("approval actor does not match the requested action")
            payload = json.loads(existing["payload_json"])
            return GmailDraftReceipt(draft_id=payload["draft_id"], idempotency_key=idempotency_key, replayed=True)

        transport = self.transport
        if transport is None:
            raise ValueError("execute() requires a transport to reach Gmail")
        message_id = _draft_message_id(approval_id)
        if approval.state == "consumed":
            created = transport.find_draft_by_message_id(message_id=message_id)
            if created is None:
                raise RuntimeError(
                    "Gmail draft outcome is unknown after a consumed approval; create a new preview instead of retrying"
                )
        else:
            approval = self.approvals.consume(approval_id, actor=actor, token=token)
            created = transport.create_draft(message_id=message_id, **approval.preview)
        draft_id = created.get("id")
        if not isinstance(draft_id, str) or not draft_id:
            raise ValueError("Gmail did not return a draft id")
        payload = {"draft_id": draft_id}
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
        return GmailDraftReceipt(draft_id=draft_id, idempotency_key=idempotency_key, replayed=False)


class GmailSendReceipt(BaseModel):
    message_id: str
    idempotency_key: str
    replayed: bool


class GmailSendActions:
    """Always-human-approved Gmail send action; it is never scheduled or automatic."""

    connector_name = "gmail"
    action_type = "gmail_message_send"

    def __init__(self, database: Database, approvals: ApprovalService, transport: GmailWriteTransport | None = None) -> None:
        self.database = database
        self.approvals = approvals
        self.transport = transport

    def propose_send(self, *, actor: str, to: str, subject: str, body: str) -> Approval:
        if not to.strip() or not subject.strip() or not body.strip():
            raise ValueError("recipient, subject, and body must not be empty")
        return self.approvals.propose(
            actor=actor, action_type=self.action_type, preview={"to": to.strip(), "subject": subject.strip(), "body": body}
        )

    def execute(self, approval_id: str, *, actor: str, token: str) -> GmailSendReceipt:
        self.database.migrate()
        idempotency_key = f"{self.action_type}:{approval_id}"
        approval = self.approvals.verify(approval_id, actor=actor, token=token)
        if approval.action_type != self.action_type:
            raise PolicyError("approval is not for Gmail send")
        with self.database.connect() as connection:
            existing = connection.execute("SELECT actor, payload_json FROM action_receipts WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        if existing is not None:
            self.approvals.verify(approval_id, actor=actor, token=token)
            if existing["actor"] != actor:
                raise PolicyError("approval actor does not match the requested action")
            return GmailSendReceipt(message_id=json.loads(existing["payload_json"])["message_id"], idempotency_key=idempotency_key, replayed=True)
        if self.transport is None:
            raise ValueError("execute() requires a transport to reach Gmail")
        message_id = _sent_message_id(approval_id)
        if approval.state == "consumed":
            created = self.transport.find_sent_message_by_message_id(message_id=message_id)
            if created is None:
                raise RuntimeError(
                    "Gmail send outcome is unknown after a consumed approval; create a new preview instead of retrying"
                )
        else:
            self.approvals.consume(approval_id, actor=actor, token=token)
            created = self.transport.send_message(message_id=message_id, **approval.preview)
        message_id = created.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("Gmail did not return a message id")
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """INSERT INTO action_receipts (idempotency_key, connector, action_type, approval_id, actor, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (idempotency_key, self.connector_name, self.action_type, approval_id, actor,
                     json.dumps({"message_id": message_id}, sort_keys=True), datetime.now(UTC).isoformat()),
                )
        return GmailSendReceipt(message_id=message_id, idempotency_key=idempotency_key, replayed=False)


def _normalize_message(item: dict[str, Any]) -> dict[str, Any]:
    message_id = item.get("id")
    internal_date = item.get("internalDate")
    if not isinstance(message_id, str) or not message_id or not isinstance(internal_date, str):
        raise ValueError("Gmail message is missing id or internalDate")
    occurred_at = datetime.fromtimestamp(int(internal_date) / 1000, tz=UTC)
    headers = parse_message_headers(item.get("payload"))
    subject = headers.get("subject") or "(no subject)"
    snippet = item.get("snippet")
    return {
        "source": "gmail",
        # A Gmail message body never changes once received, so the ID alone is a stable key.
        "external_id": message_id,
        "occurred_at": occurred_at,
        "content": subject,
        "metadata": {
            "message_id": message_id,
            "from": headers.get("from"),
            "snippet": snippet if isinstance(snippet, str) else None,
            "html_url": f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        },
        "sensitivity": "personal",
    }


def parse_message_headers(payload: object) -> dict[str, str]:
    """Lower-case header lookup shared with the inbound command gateway."""
    if not isinstance(payload, dict):
        return {}
    raw_headers = payload.get("headers")
    if not isinstance(raw_headers, list):
        return {}
    result: dict[str, str] = {}
    for header in raw_headers:
        if isinstance(header, dict) and isinstance(header.get("name"), str) and isinstance(header.get("value"), str):
            result[header["name"].lower()] = header["value"]
    return result


def _draft_message_id(approval_id: str) -> str:
    """Return an RFC 2822 Message-ID used only to recover one local draft action."""
    return f"<alfred-{approval_id}@local.invalid>"


def _sent_message_id(approval_id: str) -> str:
    """Return an RFC 2822 Message-ID used only to recover one send action."""
    return f"<alfred-send-{approval_id}@local.invalid>"
