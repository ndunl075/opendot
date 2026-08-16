"""Gmail thread metadata, additive backfill, and awaiting-reply report."""

from __future__ import annotations

import json
from pathlib import Path

from alfred.connector_records import ConnectorRecordStore
from alfred.db import Database
from alfred.gmail import GmailSync
from alfred.gmail_backfill import GmailThreadBackfill
from alfred.hermes_bridge import _low_priority_mail
from alfred.threads import ThreadService


def _message(
    message_id: str,
    *,
    thread_id: str = "t1",
    subject: str = "Re: capstone",
    list_unsubscribe: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": "advisor@school.example"},
    ]
    if list_unsubscribe is not None:
        headers.append({"name": "List-Unsubscribe", "value": list_unsubscribe})
    return {
        "id": message_id,
        "threadId": thread_id,
        "internalDate": "1786190400000",
        "labelIds": labels or ["INBOX", "UNREAD", "CATEGORY_PERSONAL"],
        "snippet": "Quick question",
        "payload": {"headers": headers},
    }


class FakeGmail:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = messages

    def list_unread_inbox(self, *, limit=500):
        return self.messages[:limit]


class FakeMetadata:
    def __init__(self, by_id: dict[str, dict]) -> None:
        self.by_id = by_id
        self.calls: list[str] = []

    def get_message_metadata(self, message_id: str) -> dict:
        self.calls.append(message_id)
        return self.by_id[message_id]


def test_gmail_sync_stores_thread_id_and_list_unsubscribe(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    GmailSync(
        database,
        FakeGmail(
            [
                _message(
                    "1",
                    list_unsubscribe="<mailto:unsub@news.example>",
                    labels=["INBOX", "UNREAD", "CATEGORY_PERSONAL"],
                )
            ]
        ),
    ).sync()

    with database.connect() as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM connector_records WHERE record_id = '1'"
            ).fetchone()[0]
        )
    assert payload["thread_id"] == "t1"
    assert payload["list_unsubscribe"] == "<mailto:unsub@news.example>"


def test_backfill_only_fills_missing_fields(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            ConnectorRecordStore.replace_snapshot(
                connection,
                connector="gmail",
                account="self",
                record_type="unread_message",
                records={
                    "old": {
                        "subject": "Hello",
                        "from": "a@b.c",
                        "snippet": "hi",
                        "html_url": "https://mail.google.com/mail/u/0/#inbox/old",
                    }
                },
            )

    result = GmailThreadBackfill(
        database,
        FakeMetadata(
            {
                "old": _message(
                    "old",
                    thread_id="thread-9",
                    list_unsubscribe="<https://news.example/unsub>",
                    labels=["INBOX", "UNREAD", "CATEGORY_UPDATES"],
                )
            }
        ),
    ).run()

    assert (result.examined, result.repaired, result.failed) == (1, 1, 0)
    with database.connect() as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM connector_records WHERE record_id = 'old'"
            ).fetchone()[0]
        )
    assert payload["subject"] == "Hello"  # never overwritten
    assert payload["thread_id"] == "thread-9"
    assert payload["list_unsubscribe"] == "<https://news.example/unsub>"
    assert payload["label_ids"] == ["INBOX", "UNREAD", "CATEGORY_UPDATES"]


def test_backfill_does_not_overwrite_existing_thread_id(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            ConnectorRecordStore.replace_snapshot(
                connection,
                connector="gmail",
                account="self",
                record_type="unread_message",
                records={
                    "kept": {
                        "subject": "Hello",
                        "from": "a@b.c",
                        "snippet": "hi",
                        "thread_id": "original",
                        "label_ids": ["INBOX"],
                        "list_unsubscribe": None,
                        "html_url": "https://mail.google.com/mail/u/0/#inbox/kept",
                    }
                },
            )

    result = GmailThreadBackfill(
        database,
        FakeMetadata({"kept": _message("kept", thread_id="different")}),
    ).run()

    assert result.repaired == 0
    with database.connect() as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM connector_records WHERE record_id = 'kept'"
            ).fetchone()[0]
        )
    assert payload["thread_id"] == "original"


def test_thread_report_suppresses_list_unsubscribe_newsletters(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            ConnectorRecordStore.replace_snapshot(
                connection,
                connector="gmail",
                account="self",
                record_type="unread_message",
                records={
                    "news": {
                        "subject": "Weekly picks",
                        "from": "news@shop.example",
                        "snippet": "Sale",
                        "thread_id": "bulk",
                        "label_ids": ["INBOX", "UNREAD", "CATEGORY_PERSONAL"],
                        "list_unsubscribe": "<mailto:bye@shop.example>",
                        "html_url": "https://mail.google.com/mail/u/0/#inbox/news",
                    },
                    "human": {
                        "subject": "Can you review?",
                        "from": "advisor@school.example",
                        "snippet": "When free?",
                        "thread_id": "real",
                        "label_ids": ["INBOX", "UNREAD", "CATEGORY_PERSONAL"],
                        "list_unsubscribe": None,
                        "html_url": "https://mail.google.com/mail/u/0/#inbox/human",
                    },
                },
            )

    report = ThreadService(database).awaiting_reply()

    assert [item.thread_id for item in report.awaiting_reply] == ["real"]
    assert report.suppressed_bulk == 1
    assert "Can you review?" in report.render()
    assert "Weekly picks" not in report.render()


def test_list_unsubscribe_marks_mail_low_priority() -> None:
    assert _low_priority_mail(
        {
            "subject": "This week's digest",
            "from": "hello@brand.example",
            "snippet": "New drops",
            "label_ids": ["CATEGORY_PERSONAL"],
            "list_unsubscribe": "<mailto:unsub@brand.example>",
        }
    )
    assert not _low_priority_mail(
        {
            "subject": "Quick question",
            "from": "advisor@school.example",
            "snippet": "Thoughts?",
            "label_ids": ["CATEGORY_PERSONAL"],
            "list_unsubscribe": None,
        }
    )
