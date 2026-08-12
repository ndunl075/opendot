"""Append-only, tamper-evident audit records for Alfred actions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .db import Database


class AuditEvent(BaseModel):
    """A redacted record of a tool run or policy decision."""

    actor: str
    client: str = "cli"
    tool: str
    outcome: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None


class AuditRecord(BaseModel):
    """A stored record read back for a read-only surface (e.g. the admin UI)."""

    id: str
    occurred_at: datetime
    actor: str
    client: str
    tool: str
    outcome: str
    result: dict[str, Any]
    correlation_id: str | None


class AuditLog:
    """Writes and verifies the hash chain stored in ``tool_runs``."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def append(self, event: AuditEvent) -> str:
        """Append a record atomically and return its immutable ID."""
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                return self.append_in_transaction(connection, event)

    @classmethod
    def append_in_transaction(cls, connection: sqlite3.Connection, event: AuditEvent) -> str:
        """Append while sharing the caller's transaction with the actual action."""
        record_id = str(uuid4())
        occurred_at = datetime.now(UTC).isoformat()
        previous = connection.execute(
            "SELECT record_hash FROM tool_runs ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous["record_hash"] if previous else None
        payload = {
            "id": record_id,
            "occurred_at": occurred_at,
            "actor": event.actor,
            "client": event.client,
            "tool": event.tool,
            "outcome": event.outcome,
            "arguments": event.arguments,
            "result": event.result,
            "correlation_id": event.correlation_id,
            "previous_hash": previous_hash,
        }
        record_hash = cls._hash(payload)
        connection.execute(
            """
            INSERT INTO tool_runs (
                id, occurred_at, actor, client, tool, outcome, arguments_json,
                result_json, correlation_id, previous_hash, record_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                occurred_at,
                event.actor,
                event.client,
                event.tool,
                event.outcome,
                cls._json(event.arguments),
                cls._json(event.result),
                event.correlation_id,
                previous_hash,
                record_hash,
            ),
        )
        return record_id

    def recent(self, *, limit: int = 50) -> list[AuditRecord]:
        """Return the most recent records, newest first, for a read-only surface like the admin UI."""
        self.database.migrate()
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM tool_runs ORDER BY sequence DESC LIMIT ?", (limit,)).fetchall()
        return [
            AuditRecord(
                id=row["id"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                actor=row["actor"],
                client=row["client"],
                tool=row["tool"],
                outcome=row["outcome"],
                result=json.loads(row["result_json"]),
                correlation_id=row["correlation_id"],
            )
            for row in rows
        ]

    def verify(self) -> bool:
        """Verify the complete local audit hash chain."""
        self.database.migrate()
        with self.database.connect() as connection:
            records = connection.execute("SELECT * FROM tool_runs ORDER BY sequence ASC").fetchall()
        expected_previous_hash: str | None = None
        for record in records:
            if record["previous_hash"] != expected_previous_hash:
                return False
            payload = {
                "id": record["id"],
                "occurred_at": record["occurred_at"],
                "actor": record["actor"],
                "client": record["client"],
                "tool": record["tool"],
                "outcome": record["outcome"],
                "arguments": json.loads(record["arguments_json"]),
                "result": json.loads(record["result_json"]),
                "correlation_id": record["correlation_id"],
                "previous_hash": record["previous_hash"],
            }
            if self._hash(payload) != record["record_hash"]:
                return False
            expected_previous_hash = record["record_hash"]
        return True

    @staticmethod
    def _json(value: dict[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def _hash(cls, payload: dict[str, Any]) -> str:
        return hashlib.sha256(cls._json(payload).encode("utf-8")).hexdigest()
