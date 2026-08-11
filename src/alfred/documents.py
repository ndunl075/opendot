"""Documents: the raw-artifact layer over an event, per ARCHITECTURE.md section 4.

A document is not the memory extracted from a file; it is the file itself --
its URI/path, MIME type, and checksum -- linked to the event that observed
it. Alfred stores this pointer, never the file's bytes, matching the
"documents/chunks ... linked to raw events" layer distinct from derived
memory.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel


class Document(BaseModel):
    id: str
    event_id: str
    uri: str
    mime_type: str | None
    checksum: str
    retention_policy: str | None


class DocumentStore:
    @staticmethod
    def append(
        connection: sqlite3.Connection,
        *,
        event_id: str,
        uri: str,
        checksum: str,
        mime_type: str | None = None,
        retention_policy: str | None = None,
    ) -> Document:
        """Record one raw-artifact pointer for an already-stored event."""
        document_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO documents (id, event_id, uri, mime_type, checksum, retention_policy, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (document_id, event_id, uri, mime_type, checksum, retention_policy, datetime.now(UTC).isoformat()),
        )
        return Document(
            id=document_id,
            event_id=event_id,
            uri=uri,
            mime_type=mime_type,
            checksum=checksum,
            retention_policy=retention_policy,
        )

    @staticmethod
    def for_event(connection: sqlite3.Connection, event_id: str) -> list[Document]:
        rows = connection.execute("SELECT * FROM documents WHERE event_id = ? ORDER BY created_at", (event_id,)).fetchall()
        return [
            Document(
                id=row["id"],
                event_id=row["event_id"],
                uri=row["uri"],
                mime_type=row["mime_type"],
                checksum=row["checksum"],
                retention_policy=row["retention_policy"],
            )
            for row in rows
        ]
