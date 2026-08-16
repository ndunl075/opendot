"""Additive repair of Gmail connector_records missing thread metadata.

Older unread snapshots stored only subject/from/snippet/label_ids/html_url.
Thread reports and newsletter filtering need ``thread_id`` and
``list_unsubscribe`` too. This backfill re-fetches metadata for rows that are
missing those fields and writes *only* the missing keys — never content,
timestamps, existing values, or the immutable event log (decision 12).
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .db import Database
from .gmail import parse_message_headers


class GmailMetadataTransport(Protocol):
    def get_message_metadata(self, message_id: str) -> dict[str, Any]: ...


class GmailBackfillResult(BaseModel):
    examined: int
    repaired: int
    failed: int
    skipped: int


class GmailClientMetadataAdapter:
    """Adapt ``GmailClient._get_message`` to the backfill transport surface."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get_message_metadata(self, message_id: str) -> dict[str, Any]:
        return self._client._get_message(message_id)


class GmailThreadBackfill:
    """Fill missing thread_id / list_unsubscribe / label_ids on unread rows."""

    connector_name = "gmail"
    account_name = "self"
    record_type = "unread_message"

    def __init__(self, database: Database, transport: GmailMetadataTransport) -> None:
        self.database = database
        self.transport = transport

    def run(self, *, limit: int | None = None) -> GmailBackfillResult:
        self.database.migrate()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT record_id, payload_json
                FROM connector_records
                WHERE connector = ? AND account = ? AND record_type = ?
                ORDER BY observed_at DESC, record_id
                """,
                (self.connector_name, self.account_name, self.record_type),
            ).fetchall()
        examined = repaired = failed = skipped = 0
        for row in rows:
            if limit is not None and examined >= limit:
                break
            examined += 1
            payload = json.loads(row["payload_json"])
            if not _needs_repair(payload):
                skipped += 1
                continue
            try:
                remote = self.transport.get_message_metadata(str(row["record_id"]))
                patch = _metadata_patch(remote)
            except Exception:
                failed += 1
                continue
            if not patch:
                skipped += 1
                continue
            merged = _merge_additive(payload, patch)
            if merged == payload:
                skipped += 1
                continue
            with self.database.connect() as connection:
                with self.database.transaction(connection):
                    connection.execute(
                        """
                        UPDATE connector_records
                        SET payload_json = ?
                        WHERE connector = ? AND account = ? AND record_type = ? AND record_id = ?
                        """,
                        (
                            json.dumps(merged, sort_keys=True),
                            self.connector_name,
                            self.account_name,
                            self.record_type,
                            row["record_id"],
                        ),
                    )
                    AuditLog.append_in_transaction(
                        connection,
                        AuditEvent(
                            actor="system:gmail",
                            client="gmail",
                            tool="gmail_thread_backfill",
                            outcome="ok",
                            arguments={"record_id": row["record_id"]},
                            result={"filled": sorted(patch.keys())},
                            correlation_id=str(row["record_id"]),
                        ),
                    )
            repaired += 1
        return GmailBackfillResult(
            examined=examined, repaired=repaired, failed=failed, skipped=skipped
        )


def _needs_repair(payload: dict[str, Any]) -> bool:
    if not payload.get("thread_id"):
        return True
    if "list_unsubscribe" not in payload:
        return True
    labels = payload.get("label_ids")
    if not isinstance(labels, list):
        return True
    return False


def _metadata_patch(item: dict[str, Any]) -> dict[str, Any]:
    headers = parse_message_headers(item.get("payload"))
    patch: dict[str, Any] = {}
    thread_id = item.get("threadId")
    if isinstance(thread_id, str) and thread_id:
        patch["thread_id"] = thread_id
    raw_label_ids = item.get("labelIds")
    if isinstance(raw_label_ids, list):
        patch["label_ids"] = [label for label in raw_label_ids if isinstance(label, str)]
    list_unsubscribe = headers.get("list-unsubscribe")
    # Always set the key once known, including explicit null, so a repaired
    # row is not re-fetched forever looking for a header it never had.
    patch["list_unsubscribe"] = list_unsubscribe if list_unsubscribe else None
    return patch


def _merge_additive(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Copy ``patch`` keys into ``existing`` only where the old value is empty."""
    merged = dict(existing)
    for key, value in patch.items():
        if key == "list_unsubscribe":
            if key not in merged:
                merged[key] = value
            continue
        current = merged.get(key)
        if current in (None, "", []):
            merged[key] = value
    return merged
