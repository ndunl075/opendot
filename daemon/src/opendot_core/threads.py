"""Unread Gmail threads that look like they are waiting for a reply.

Groups active unread connector_records by ``thread_id`` and drops bulk mail
that carries a List-Unsubscribe header. Live data showed Gmail's own
CATEGORY_PERSONAL label on most newsletters, so category labels alone cannot
drive this report — the unsubscribe header is the reliable signal.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from .db import Database


class ThreadSummary(BaseModel):
    thread_id: str
    subject: str
    from_address: str | None
    message_count: int
    latest_html_url: str | None
    label_ids: list[str] = []


class ThreadReport(BaseModel):
    generated_at: datetime
    awaiting_reply: list[ThreadSummary]
    suppressed_bulk: int = 0
    missing_thread_id: int = 0

    def render(self) -> str:
        lines = [
            f"Threads awaiting reply — {self.generated_at.date().isoformat()}",
            f"Counted {len(self.awaiting_reply)} thread(s)"
            + (
                f"; suppressed {self.suppressed_bulk} bulk"
                if self.suppressed_bulk
                else ""
            )
            + (
                f"; {self.missing_thread_id} unread still missing thread_id"
                if self.missing_thread_id
                else ""
            )
            + ".",
        ]
        if not self.awaiting_reply:
            lines.append("\nNothing looks like it needs a reply.")
            return "\n".join(lines)
        lines.append("")
        for item in self.awaiting_reply:
            who = f" — {item.from_address}" if item.from_address else ""
            count = f" ({item.message_count} unread)" if item.message_count > 1 else ""
            link = f" <{item.latest_html_url}>" if item.latest_html_url else ""
            lines.append(f"- {item.subject}{who}{count}{link}")
        return "\n".join(lines)


class ThreadService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def awaiting_reply(self, *, now: datetime | None = None) -> ThreadReport:
        """Unread personal threads, excluding List-Unsubscribe newsletters."""
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        self.database.migrate()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT record_id, payload_json, observed_at
                FROM connector_records
                WHERE connector = 'gmail' AND account = 'self'
                  AND record_type = 'unread_message' AND active = 1
                ORDER BY observed_at DESC, record_id
                """
            ).fetchall()
        by_thread: dict[str, list[dict[str, Any]]] = {}
        suppressed_bulk = 0
        missing_thread_id = 0
        for row in rows:
            payload = json.loads(row["payload_json"])
            if _is_bulk(payload):
                suppressed_bulk += 1
                continue
            thread_id = payload.get("thread_id")
            if not isinstance(thread_id, str) or not thread_id:
                missing_thread_id += 1
                continue
            by_thread.setdefault(thread_id, []).append(payload)

        awaiting: list[ThreadSummary] = []
        for thread_id, messages in by_thread.items():
            latest = messages[0]
            labels: list[str] = []
            for message in messages:
                raw = message.get("label_ids")
                if isinstance(raw, list):
                    labels.extend(label for label in raw if isinstance(label, str))
            awaiting.append(
                ThreadSummary(
                    thread_id=thread_id,
                    subject=str(latest.get("subject") or "(no subject)"),
                    from_address=latest.get("from") if isinstance(latest.get("from"), str) else None,
                    message_count=len(messages),
                    latest_html_url=(
                        latest.get("html_url")
                        if isinstance(latest.get("html_url"), str)
                        else None
                    ),
                    label_ids=sorted(set(labels)),
                )
            )
        awaiting.sort(key=lambda item: (item.subject.lower(), item.thread_id))
        return ThreadReport(
            generated_at=generated_at,
            awaiting_reply=awaiting,
            suppressed_bulk=suppressed_bulk,
            missing_thread_id=missing_thread_id,
        )


def _is_bulk(payload: dict[str, Any]) -> bool:
    """True when the message advertises a bulk unsubscribe path."""
    value = payload.get("list_unsubscribe")
    return isinstance(value, str) and bool(value.strip())
