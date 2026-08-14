"""Conservative, provenance-first learning from ordinary conversation.

Cognee's useful idea is a pipeline rather than a magical database: observe,
extract, corroborate, promote, retrieve, and improve from feedback. Alfred
keeps that loop inside its existing SQLite authority. An extractor can
propose facts, but deterministic policy decides what becomes recallable.

The default extractor is deliberately small and local. It recognizes explicit
"remember" statements plus common preference/identity/goal phrasing without a
model call. A future local-model extractor can implement ``MemoryExtractor``
without changing storage or promotion policy.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from .audit import AuditEvent, AuditLog
from .db import Database


class MemoryProposal(BaseModel):
    statement: str
    kind: str = "note"
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    explicit: bool = False
    sensitivity: str = "personal"


class MemoryExtractor(Protocol):
    version: str

    def extract(self, text: str) -> list[MemoryProposal]: ...


class MemoryLearningResult(BaseModel):
    processed_events: int = 0
    proposed: int = 0
    created_candidates: int = 0
    promoted: int = 0
    already_known: int = 0


_SPACE = re.compile(r"\s+")
_SECRET = re.compile(
    r"\b(?:password|passcode|api[ _-]?key|secret|access token|private key|"
    r"recovery code|seed phrase|social security|ssn|credit card)\b",
    re.IGNORECASE,
)
_SENSITIVE = re.compile(
    r"\b(?:diagnos(?:is|ed)|medication|therapy|therapist|mental health|"
    r"bank account|salary|income|medical|health condition)\b",
    re.IGNORECASE,
)


class RuleBasedMemoryExtractor:
    """Fast extraction of high-precision first-person durable statements."""

    version = "rules-v1"

    _patterns: tuple[tuple[re.Pattern[str], str, float, bool, str], ...] = (
        (
            re.compile(r"^\s*(?:please\s+)?remember(?:\s+that)?\s+(.+?)\s*[.!?]*$", re.IGNORECASE),
            "note",
            1.0,
            True,
            "{value}",
        ),
        (
            re.compile(r"\b(?:i\s+prefer|my\s+preference\s+is)\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
            "preference",
            0.82,
            False,
            "The user prefers {value}.",
        ),
        (
            re.compile(r"\bi\s+(love|like|dislike|hate)\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
            "preference",
            0.72,
            False,
            "The user {verb}s {value}.",
        ),
        (
            re.compile(
                r"\bmy\s+(name|timezone|time zone|pronouns|birthday|home city|location)\s+is\s+(.+?)(?:[.!?]|$)",
                re.IGNORECASE,
            ),
            "identity",
            0.88,
            False,
            "The user's {field} is {value}.",
        ),
        (
            re.compile(r"\bi\s+want\s+to\s+(.+?)(?:[.!?]|$)", re.IGNORECASE),
            "goal",
            0.58,
            False,
            "The user wants to {value}.",
        ),
        (
            re.compile(
                r"\bmy\s+(?P<item>[^;.!?]+?)\s+is\s+due\s+(?P<when>[^;.!?]+)",
                re.IGNORECASE,
            ),
            "deadline",
            1.0,
            True,
            "The user's {item} is due {when}.",
        ),
    )

    def extract(self, text: str) -> list[MemoryProposal]:
        if not text.strip() or text.lstrip().startswith("/"):
            return []
        proposals: list[MemoryProposal] = []
        for pattern, kind, confidence, explicit, template in self._patterns:
            for match in pattern.finditer(text):
                groups = match.groupdict()
                if groups:
                    statement = template.format(**{key: value.strip() for key, value in groups.items()})
                elif kind == "preference" and len(match.groups()) == 2:
                    verb, value = match.groups()
                    statement = template.format(verb=verb.lower(), value=value.strip())
                elif kind == "identity" and len(match.groups()) == 2:
                    field, value = match.groups()
                    statement = template.format(field=field.lower(), value=value.strip())
                else:
                    statement = template.format(value=match.group(1).strip())
                statement = _clean_statement(statement)
                if not statement or _SECRET.search(statement):
                    continue
                sensitivity = "sensitive" if _SENSITIVE.search(statement) else "personal"
                proposals.append(
                    MemoryProposal(
                        statement=statement,
                        kind=kind,
                        confidence=confidence,
                        explicit=explicit,
                        sensitivity=sensitivity,
                    )
                )
        # One source statement must not count twice just because patterns overlap.
        unique: dict[str, MemoryProposal] = {}
        for proposal in proposals:
            unique.setdefault(_normalized_key(proposal.kind, proposal.statement), proposal)
        return list(unique.values())[:5]


class MemoryLearningService:
    """Turn unprocessed user events into corroborated, inspectable memories."""

    def __init__(
        self,
        database: Database,
        extractor: MemoryExtractor | None = None,
        *,
        promotion_observations: int = 2,
    ) -> None:
        if promotion_observations < 2:
            raise ValueError("implicit memory promotion needs at least two observations")
        self.database = database
        self.extractor = extractor or RuleBasedMemoryExtractor()
        self.promotion_observations = promotion_observations

    def run_once(self, *, limit: int = 20) -> MemoryLearningResult:
        self.database.migrate()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT e.id, e.content
                FROM events e
                LEFT JOIN memory_learning_runs r ON r.source_event_id = e.id
                WHERE e.source IN ('telegram', 'slack')
                  AND e.content IS NOT NULL
                  AND r.source_event_id IS NULL
                ORDER BY e.occurred_at, e.id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        result = MemoryLearningResult()
        for row in rows:
            try:
                proposals = self.extractor.extract(str(row["content"]))
                counts = self._store_event(str(row["id"]), proposals)
            except Exception as error:
                self._record_failure(str(row["id"]), error)
                continue
            result.processed_events += 1
            result.proposed += len(proposals)
            result.created_candidates += counts["created"]
            result.promoted += counts["promoted"]
            result.already_known += counts["known"]
        return result

    def _store_event(self, source_event_id: str, proposals: list[MemoryProposal]) -> dict[str, int]:
        created = promoted = known = 0
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                for proposal in proposals:
                    outcome = self._observe(connection, source_event_id, proposal, now)
                    created += int(outcome == "created")
                    promoted += int(outcome == "promoted")
                    known += int(outcome == "known")
                connection.execute(
                    """
                    INSERT INTO memory_learning_runs (
                        source_event_id, extractor_version, outcome, proposals, created_at
                    ) VALUES (?, ?, 'processed', ?, ?)
                    """,
                    (source_event_id, self.extractor.version, len(proposals), now),
                )
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="system:memory_learning",
                        client="memory_learning",
                        tool="memory_learning",
                        outcome="ok",
                        result={
                            "source_event_id": source_event_id,
                            "proposals": len(proposals),
                            "created": created,
                            "promoted": promoted,
                        },
                    ),
                )
        return {"created": created, "promoted": promoted, "known": known}

    def _observe(
        self,
        connection: sqlite3.Connection,
        source_event_id: str,
        proposal: MemoryProposal,
        now: str,
    ) -> str:
        statement = _clean_statement(proposal.statement)
        if not statement or len(statement) > 500 or proposal.kind not in {"note", "preference", "identity", "goal", "deadline"}:
            return "ignored"
        key = _normalized_key(proposal.kind, statement)
        candidate = connection.execute(
            "SELECT * FROM memory_learning_candidates WHERE normalized_key = ?", (key,)
        ).fetchone()
        if candidate is None:
            memory_id = str(uuid4())
            # Explicit "remember" is direct user authorization. Everything
            # inferred remains quarantined until an independent repetition.
            confirmed = proposal.explicit and proposal.sensitivity != "sensitive"
            status = "confirmed" if confirmed else "candidate"
            connection.execute(
                """
                INSERT INTO memories (
                    id, kind, statement, status, source_event_id, domains_json,
                    sensitivity, confidence, confirmed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    proposal.kind,
                    statement,
                    status,
                    source_event_id,
                    proposal.sensitivity,
                    proposal.confidence,
                    int(confirmed),
                    now,
                    now,
                ),
            )
            if confirmed:
                connection.execute(
                    "INSERT INTO memory_fts (memory_id, statement) VALUES (?, ?)",
                    (memory_id, statement),
                )
            connection.execute(
                """
                INSERT INTO memory_learning_candidates (
                    normalized_key, memory_id, observation_count, first_observed_at, last_observed_at
                ) VALUES (?, ?, 1, ?, ?)
                """,
                (key, memory_id, now, now),
            )
            self._add_observation(connection, memory_id, source_event_id, proposal, statement, now)
            return "promoted" if confirmed else "created"

        memory_id = str(candidate["memory_id"])
        inserted = self._add_observation(
            connection, memory_id, source_event_id, proposal, statement, now
        )
        if not inserted:
            return "known"
        count = int(candidate["observation_count"]) + 1
        connection.execute(
            """
            UPDATE memory_learning_candidates
            SET observation_count = ?, last_observed_at = ?
            WHERE normalized_key = ?
            """,
            (count, now, key),
        )
        memory = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if (
            memory is not None
            and memory["status"] == "candidate"
            and memory["sensitivity"] in {"public", "personal"}
            and count >= self.promotion_observations
        ):
            connection.execute(
                "UPDATE memories SET status = 'confirmed', confirmed = 1, updated_at = ? WHERE id = ?",
                (now, memory_id),
            )
            connection.execute(
                "INSERT INTO memory_fts (memory_id, statement) VALUES (?, ?)",
                (memory_id, memory["statement"]),
            )
            connection.execute(
                """
                INSERT INTO memory_history (
                    id, memory_id, previous_status, next_status, actor, reason, created_at
                ) VALUES (?, ?, 'candidate', 'confirmed', 'system:memory_learning', ?, ?)
                """,
                (str(uuid4()), memory_id, f"corroborated by {count} source events", now),
            )
            return "promoted"
        return "known"

    def _add_observation(
        self,
        connection: sqlite3.Connection,
        memory_id: str,
        source_event_id: str,
        proposal: MemoryProposal,
        statement: str,
        now: str,
    ) -> bool:
        cursor = connection.execute(
            """
            INSERT INTO memory_learning_observations (
                id, memory_id, source_event_id, extractor_version, confidence, explicit, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id, source_event_id) DO NOTHING
            """,
            (
                str(uuid4()),
                memory_id,
                source_event_id,
                self.extractor.version,
                proposal.confidence,
                int(proposal.explicit),
                now,
            ),
        )
        if cursor.rowcount != 1:
            return False
        connection.execute(
            """
            INSERT INTO evidence (
                id, subject_kind, subject_id, source_event_id, extraction_version,
                excerpt_hash, created_at
            ) VALUES (?, 'memory', ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                memory_id,
                source_event_id,
                self.extractor.version,
                hashlib.sha256(statement.encode("utf-8")).hexdigest(),
                now,
            ),
        )
        return True

    def _record_failure(self, source_event_id: str, error: Exception) -> None:
        now = datetime.now(UTC).isoformat()
        detail = f"{error.__class__.__name__}: {error}"[:500]
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    INSERT INTO memory_learning_runs (
                        source_event_id, extractor_version, outcome, proposals, detail, created_at
                    ) VALUES (?, ?, 'error', 0, ?, ?)
                    ON CONFLICT(source_event_id) DO NOTHING
                    """,
                    (source_event_id, self.extractor.version, detail, now),
                )


class MemoryFeedbackStore:
    """Append retrieval outcomes so ranking/extraction changes can be evaluated."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def record(
        self,
        memory_id: str,
        *,
        query: str,
        outcome: str,
        actor: str,
        source_event_id: str | None = None,
    ) -> dict[str, str]:
        if outcome not in {"relevant", "irrelevant", "incorrect"}:
            raise ValueError("memory feedback outcome must be relevant, irrelevant, or incorrect")
        if not query.strip():
            raise ValueError("memory feedback query cannot be empty")
        self.database.migrate()
        feedback_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                memory = connection.execute("SELECT id FROM memories WHERE id = ?", (memory_id,)).fetchone()
                if memory is None:
                    raise ValueError(f"memory does not exist: {memory_id}")
                connection.execute(
                    """
                    INSERT INTO memory_retrieval_feedback (
                        id, memory_id, query, outcome, source_event_id, actor, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (feedback_id, memory_id, query.strip(), outcome, source_event_id, actor, now),
                )
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor=actor,
                        client="memory_learning",
                        tool="memory_feedback",
                        outcome="ok",
                        result={"feedback_id": feedback_id, "memory_id": memory_id, "outcome": outcome},
                    ),
                )
        return {"feedback_id": feedback_id, "memory_id": memory_id, "outcome": outcome}


def _clean_statement(statement: str) -> str:
    value = _SPACE.sub(" ", statement).strip(" \t\r\n")
    if len(value) < 4:
        return ""
    return value[0].upper() + value[1:]


def _normalized_key(kind: str, statement: str) -> str:
    canonical = _SPACE.sub(" ", statement).strip().casefold().rstrip(".!?")
    return hashlib.sha256(f"{kind}:{canonical}".encode("utf-8")).hexdigest()
