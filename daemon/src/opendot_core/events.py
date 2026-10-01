"""Immutable source-event ingestion with connector-level deduplication."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel


class StoredEvent(BaseModel):
    """An event that was newly stored or recovered through idempotency."""

    id: str
    is_new: bool


class EventStore:
    """Append source events without silently collapsing repeated messages."""

    @staticmethod
    def append(
        connection: sqlite3.Connection,
        *,
        source: str,
        external_id: str,
        occurred_at: datetime,
        content: str,
        metadata: dict[str, Any],
        sensitivity: str = "personal",
    ) -> StoredEvent:
        """Store an event once for a source/external ID pair."""
        event_id = str(uuid4())
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        metadata_json = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
        cursor = connection.execute(
            """
            INSERT INTO events (
                id, source, external_id, occurred_at, content, metadata_json,
                sensitivity, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, external_id) DO NOTHING
            """,
            (
                event_id,
                source,
                external_id,
                occurred_at.isoformat(),
                content,
                metadata_json,
                sensitivity,
                content_hash,
            ),
        )
        if cursor.rowcount == 1:
            return StoredEvent(id=event_id, is_new=True)
        existing = connection.execute(
            "SELECT id FROM events WHERE source = ? AND external_id = ?",
            (source, external_id),
        ).fetchone()
        if existing is None:
            raise RuntimeError("event insert was ignored without an existing source event")
        return StoredEvent(id=existing["id"], is_new=False)
