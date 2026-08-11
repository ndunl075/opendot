"""Persistent delivery intents. Sending is intentionally a separate concern."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel


class OutboxMessage(BaseModel):
    id: str
    destination: str
    payload: dict[str, Any]
    idempotency_key: str
    state: str


class Outbox:
    @staticmethod
    def enqueue(
        connection: sqlite3.Connection,
        *,
        destination: str,
        payload: dict[str, Any],
        idempotency_key: str,
        job_id: str | None = None,
    ) -> OutboxMessage:
        """Enqueue once and return the existing intent on a retry."""
        record_id = str(uuid4())
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        connection.execute(
            """
            INSERT INTO outbox (id, job_id, destination, payload_json, idempotency_key, state)
            VALUES (?, ?, ?, ?, ?, 'pending')
            ON CONFLICT(idempotency_key) DO NOTHING
            """,
            (record_id, job_id, destination, payload_json, idempotency_key),
        )
        row = connection.execute(
            "SELECT id, destination, payload_json, idempotency_key, state FROM outbox WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError("outbox insert was ignored without an existing delivery intent")
        return OutboxMessage(
            id=row["id"],
            destination=row["destination"],
            payload=json.loads(row["payload_json"]),
            idempotency_key=row["idempotency_key"],
            state=row["state"],
        )
