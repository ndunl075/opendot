"""Content-free response provenance and explicit Telegram feedback."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .db import Database


FEEDBACK_OUTCOMES = frozenset({"helpful", "missing_context", "wrong_context"})


class ResponseFeedbackReceipt(BaseModel):
    response_update_id: str
    outcome: str
    recorded: bool


class ResponseFeedbackService:
    """Store no prompt or answer text, only source-level retrieval provenance."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def record_context_in_transaction(
        connection: sqlite3.Connection,
        *,
        response_update_id: str,
        sources: list[str],
        freshness: dict[str, str | None],
        items: list[dict[str, Any]],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO response_context (
                response_update_id, sources_json, freshness_json, items_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(response_update_id) DO NOTHING
            """,
            (
                response_update_id,
                json.dumps(sorted(set(sources)), separators=(",", ":")),
                json.dumps(freshness, sort_keys=True, separators=(",", ":")),
                json.dumps(items, sort_keys=True, separators=(",", ":")),
                now,
            ),
        )

    @staticmethod
    def record_feedback_in_transaction(
        connection: sqlite3.Connection,
        *,
        callback_query_id: str,
        feedback_update_id: str,
        response_update_id: str,
        outcome: str,
    ) -> ResponseFeedbackReceipt:
        if outcome not in FEEDBACK_OUTCOMES:
            raise ValueError("unsupported response feedback outcome")
        now = datetime.now(UTC).isoformat()
        inserted = connection.execute(
            """
            INSERT INTO response_feedback (
                id, callback_query_id, feedback_update_id,
                response_update_id, outcome, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(response_update_id) DO NOTHING
            """,
            (
                str(uuid4()),
                callback_query_id,
                feedback_update_id,
                response_update_id,
                outcome,
                now,
            ),
        ).rowcount == 1
        if inserted:
            AuditLog.append_in_transaction(
                connection,
                AuditEvent(
                    actor="owner:telegram",
                    client="telegram",
                    tool="response_feedback",
                    outcome="recorded",
                    arguments={
                        "response_update_id": response_update_id,
                        "feedback": outcome,
                    },
                    result={"recorded": True},
                    correlation_id=response_update_id,
                ),
            )
        return ResponseFeedbackReceipt(
            response_update_id=response_update_id,
            outcome=outcome,
            recorded=inserted,
        )

    def scores(self, *, source: str, record_ids: set[str]) -> dict[str, int]:
        """Return bounded coarse feedback used only inside an existing rank tier."""
        if not record_ids:
            return {}
        self.database.migrate()
        scores = {record_id: 0 for record_id in record_ids}
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.items_json, f.outcome
                FROM response_feedback f
                JOIN response_context c
                  ON c.response_update_id = f.response_update_id
                ORDER BY f.created_at DESC
                LIMIT 200
                """
            ).fetchall()
        for row in rows:
            delta = 1 if row["outcome"] == "helpful" else -1 if row["outcome"] == "wrong_context" else 0
            if delta == 0:
                continue
            for item in json.loads(row["items_json"]):
                record_id = str(item.get("record_id") or "")
                if item.get("source") == source and record_id in scores:
                    scores[record_id] += delta
        return {
            record_id: max(-2, min(2, score))
            for record_id, score in scores.items()
            if score
        }


def feedback_keyboard(response_update_id: str) -> dict[str, list[list[dict[str, str]]]]:
    """A compact inline keyboard whose callback data stays under Telegram's 64-byte cap."""
    return {
        "inline_keyboard": [
            [{"text": "helpful", "callback_data": f"af:{response_update_id}:h"}],
            [
                {"text": "missing context", "callback_data": f"af:{response_update_id}:m"},
                {"text": "wrong context", "callback_data": f"af:{response_update_id}:w"},
            ],
        ]
    }
