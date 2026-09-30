"""Content-free response provenance and the verdicts recorded against it."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .db import Database
from .implicit_feedback import (
    SIGNAL_BUTTON,
    SIGNAL_COVERAGE,
    SIGNAL_REPLY,
    classify_reply,
    detect_context_gap,
    find_reaction_target,
)


FEEDBACK_OUTCOMES = frozenset({"helpful", "missing_context", "wrong_context"})
FEEDBACK_SIGNALS = frozenset({SIGNAL_BUTTON, SIGNAL_REPLY, SIGNAL_COVERAGE})

#: Which verdict wins when one response collected several. A tap is a stated
#: opinion, a reply is an inferred one, and coverage is Alfred grading its own
#: pack; that is also the order of how much any of them should move ranking.
_SIGNAL_PRECEDENCE = {SIGNAL_BUTTON: 0, SIGNAL_REPLY: 1, SIGNAL_COVERAGE: 2}


def _rank(row: sqlite3.Row) -> int:
    """Lower wins. An unknown signal sorts last rather than raising."""
    return _SIGNAL_PRECEDENCE.get(str(row["signal"]), len(_SIGNAL_PRECEDENCE))


class ResponseFeedbackReceipt(BaseModel):
    response_update_id: str
    outcome: str
    recorded: bool
    signal: str = SIGNAL_BUTTON


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
        response_update_id: str,
        outcome: str,
        signal: str = SIGNAL_BUTTON,
        rule: str | None = None,
        callback_query_id: str | None = None,
        feedback_update_id: str | None = None,
    ) -> ResponseFeedbackReceipt:
        """Store one verdict, whoever or whatever reached it.

        Each signal votes at most once per response. An inferred verdict
        therefore cannot shout down a second, differently derived one, and
        replaying the same update still records nothing twice.
        """
        if outcome not in FEEDBACK_OUTCOMES:
            raise ValueError("unsupported response feedback outcome")
        if signal not in FEEDBACK_SIGNALS:
            raise ValueError("unsupported response feedback signal")
        now = datetime.now(UTC).isoformat()
        inserted = connection.execute(
            """
            INSERT INTO response_feedback (
                id, signal, callback_query_id, feedback_update_id,
                response_update_id, outcome, rule, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(response_update_id, signal) DO NOTHING
            """,
            (
                str(uuid4()),
                signal,
                callback_query_id,
                feedback_update_id,
                response_update_id,
                outcome,
                rule,
                now,
            ),
        ).rowcount == 1
        if inserted:
            AuditLog.append_in_transaction(
                connection,
                AuditEvent(
                    # An inferred verdict still comes from the owner's own
                    # words; only Alfred's self-check is Alfred's own claim.
                    actor="system:alfred" if signal == SIGNAL_COVERAGE else "owner:telegram",
                    client="telegram",
                    tool="response_feedback",
                    outcome="recorded",
                    arguments={
                        "response_update_id": response_update_id,
                        "feedback": outcome,
                        "signal": signal,
                        "rule": rule,
                    },
                    result={"recorded": True},
                    correlation_id=response_update_id,
                ),
            )
        return ResponseFeedbackReceipt(
            response_update_id=response_update_id,
            outcome=outcome,
            recorded=inserted,
            signal=signal,
        )

    @classmethod
    def record_reply_signal_in_transaction(
        cls,
        connection: sqlite3.Connection,
        *,
        chat_id: int,
        user_id: int,
        feedback_update_id: str,
        text: str,
        now: datetime,
    ) -> ResponseFeedbackReceipt | None:
        """Score the previous answer from what the owner said next, if anything.

        Runs on every inbound message and stays silent for almost all of them.
        Nothing is echoed back to the chat either: the point of dropping the
        buttons was to stop making feedback a thing anyone has to notice.
        """
        verdict = classify_reply(text)
        if verdict is None:
            return None
        response_update_id = find_reaction_target(
            connection, chat_id=chat_id, user_id=user_id, now=now
        )
        if response_update_id is None:
            return None
        return cls.record_feedback_in_transaction(
            connection,
            response_update_id=response_update_id,
            outcome=verdict.outcome,
            signal=SIGNAL_REPLY,
            rule=verdict.rule,
            feedback_update_id=feedback_update_id,
        )

    @classmethod
    def record_coverage_signal_in_transaction(
        cls,
        connection: sqlite3.Connection,
        *,
        response_update_id: str,
        sources: list[str],
        freshness: dict[str, str | None],
        now: datetime | None = None,
    ) -> ResponseFeedbackReceipt | None:
        """Record the gap in a reply's own context pack, before anyone reads it."""
        verdict = detect_context_gap(sources=sources, freshness=freshness, now=now)
        if verdict is None:
            return None
        return cls.record_feedback_in_transaction(
            connection,
            response_update_id=response_update_id,
            outcome=verdict.outcome,
            signal=SIGNAL_COVERAGE,
            rule=verdict.rule,
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
                SELECT c.items_json, f.outcome, f.signal, f.response_update_id
                FROM response_feedback f
                JOIN response_context c
                  ON c.response_update_id = f.response_update_id
                ORDER BY f.created_at DESC
                LIMIT 200
                """
            ).fetchall()
        # One vote per response, even now that several detectors can reach one.
        # Without this, a turn the owner corrected *and* Alfred flagged would
        # count twice and outweigh a turn only one of them noticed.
        strongest: dict[str, sqlite3.Row] = {}
        for row in rows:
            response_update_id = str(row["response_update_id"])
            held = strongest.get(response_update_id)
            if held is None or _rank(row) < _rank(held):
                strongest[response_update_id] = row
        for row in strongest.values():
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
