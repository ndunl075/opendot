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


class AuditLog:
    """Writes and verifies the hash chain stored in ``tool_runs``."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def append(self, event: AuditEvent) -> str:
        """Append a record atomically and return its immutable ID."""
        self.database.migrate()
        record_id = str(uuid4())
        occurred_at = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
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
                record_hash = self._hash(payload)
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
                        self._json(event.arguments),
                        self._json(event.result),
                        event.correlation_id,
                        previous_hash,
                        record_hash,
                    ),
                )
        return record_id

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
