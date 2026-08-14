"""Operator-readable evaluation report over Alfred's own learning signals.

ARCHITECTURE.md's learning loop ends with "record accepted/rejected
suggestions and retrieval misses as evaluation data" and "better memory,
retrieval, policies, and evals produce safer learning at far lower cost."
Every recording half of that is built -- response feedback, memory retrieval
feedback, implicit-candidate promotion, and workflow proposals all append to
their own tables -- but nothing ever read them back, so the data could not
actually answer whether retrieval is working.

This is the read side, and only the read side. It runs no model, writes
nothing, and changes no ranking: a reporting layer over rows other services
already own, in the same shape as ``latency.py``. Deciding what to *do* about
a bad number stays a human judgment.

Content-free by construction, matching the tables it reads: outcomes,
counts, source names, and opaque record IDs. No prompt, answer, memory
statement, or query text is selected here, so the report is safe to read
over a shoulder or paste into an issue.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from .db import Database

#: Telegram's three response-feedback buttons. Listed explicitly so a report
#: shows a deliberate 0 for an outcome nobody picked, rather than silently
#: omitting the row and reading as though the outcome does not exist.
RESPONSE_OUTCOMES = ("helpful", "missing_context", "wrong_context")

#: memory_feedback's three outcomes, same reasoning as above. Ordered worst
#: to best so a rendered table reads as a severity ramp.
RETRIEVAL_OUTCOMES = ("incorrect", "irrelevant", "relevant")


class OutcomeBreakdown(BaseModel):
    """Counts per outcome plus the one ratio worth reading at a glance."""

    total: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    positive_rate: float | None = None


class SourceQuality(BaseModel):
    """How often a context source was present when feedback was given.

    Deliberately association, not attribution: a turn usually packs several
    sources, so a source appearing alongside ``wrong_context`` is a hint
    about where to look, never proof that source caused the miss.
    """

    source: str
    responses: int
    helpful: int
    missing_context: int
    wrong_context: int


class WorkflowReview(BaseModel):
    proposed: int = 0
    accepted: int = 0
    rejected: int = 0
    pending: int = 0
    acceptance_rate: float | None = None


class MemoryLearningQuality(BaseModel):
    candidates: int = 0
    promoted: int = 0
    promotion_rate: float | None = None


class EvaluationReport(BaseModel):
    generated_at: datetime
    window_days: int
    response_feedback: OutcomeBreakdown
    retrieval_feedback: OutcomeBreakdown
    sources: list[SourceQuality] = Field(default_factory=list)
    workflows: WorkflowReview
    memory_learning: MemoryLearningQuality


class EvaluationService:
    """Summarize append-only learning signals without touching any of them."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def report(self, *, window_days: int = 30) -> EvaluationReport:
        if window_days < 1:
            raise ValueError("window_days must be at least 1")
        self.database.migrate()
        cutoff = (datetime.now(UTC) - timedelta(days=window_days)).isoformat()
        with self.database.connect() as connection:
            response = self._response_feedback(connection, cutoff)
            retrieval = self._retrieval_feedback(connection, cutoff)
            sources = self._source_quality(connection, cutoff)
            workflows = self._workflows(connection, cutoff)
            memory = self._memory_learning(connection, cutoff)
        return EvaluationReport(
            generated_at=datetime.now(UTC),
            window_days=window_days,
            response_feedback=response,
            retrieval_feedback=retrieval,
            sources=sources,
            workflows=workflows,
            memory_learning=memory,
        )

    def _response_feedback(self, connection, cutoff: str) -> OutcomeBreakdown:
        rows = connection.execute(
            "SELECT outcome, COUNT(*) AS n FROM response_feedback "
            "WHERE created_at >= ? GROUP BY outcome",
            (cutoff,),
        ).fetchall()
        counts = {outcome: 0 for outcome in RESPONSE_OUTCOMES}
        for row in rows:
            counts[str(row["outcome"])] = int(row["n"])
        return _breakdown(counts, positive=("helpful",))

    def _retrieval_feedback(self, connection, cutoff: str) -> OutcomeBreakdown:
        rows = connection.execute(
            "SELECT outcome, COUNT(*) AS n FROM memory_retrieval_feedback "
            "WHERE created_at >= ? GROUP BY outcome",
            (cutoff,),
        ).fetchall()
        counts = {outcome: 0 for outcome in RETRIEVAL_OUTCOMES}
        for row in rows:
            counts[str(row["outcome"])] = int(row["n"])
        return _breakdown(counts, positive=("relevant",))

    def _source_quality(self, connection, cutoff: str) -> list[SourceQuality]:
        """Join each feedback vote back to the sources that turn actually used.

        response_context stores the pack that produced a reply and
        response_feedback stores the verdict on it; neither is useful alone.
        This is the join the two tables were designed for.
        """
        rows = connection.execute(
            """
            SELECT c.sources_json, f.outcome
            FROM response_feedback f
            JOIN response_context c ON c.response_update_id = f.response_update_id
            WHERE f.created_at >= ?
            """,
            (cutoff,),
        ).fetchall()
        tally: dict[str, Counter] = {}
        for row in rows:
            outcome = str(row["outcome"])
            try:
                sources = json.loads(row["sources_json"])
            except (TypeError, ValueError):
                continue
            if not isinstance(sources, list):
                continue
            for source in sources:
                tally.setdefault(str(source), Counter())[outcome] += 1
        return [
            SourceQuality(
                source=source,
                responses=sum(counter.values()),
                helpful=counter.get("helpful", 0),
                missing_context=counter.get("missing_context", 0),
                wrong_context=counter.get("wrong_context", 0),
            )
            # Most-voted-on first: the sources with enough signal to act on.
            for source, counter in sorted(
                tally.items(), key=lambda item: (-sum(item[1].values()), item[0])
            )
        ]

    def _workflows(self, connection, cutoff: str) -> WorkflowReview:
        rows = connection.execute(
            "SELECT state, COUNT(*) AS n FROM workflow_skill_versions "
            "WHERE created_at >= ? GROUP BY state",
            (cutoff,),
        ).fetchall()
        counts = {str(row["state"]): int(row["n"]) for row in rows}
        accepted = counts.get("accepted", 0) + counts.get("active", 0)
        rejected = counts.get("rejected", 0)
        decided = accepted + rejected
        return WorkflowReview(
            proposed=sum(counts.values()),
            accepted=accepted,
            rejected=rejected,
            pending=counts.get("pending", 0) + counts.get("draft", 0),
            # Rate over *decided* proposals only. Including still-pending ones
            # would make a healthy backlog look like rejection.
            acceptance_rate=round(accepted / decided, 3) if decided else None,
        )

    def _memory_learning(self, connection, cutoff: str) -> MemoryLearningQuality:
        candidates = int(
            connection.execute(
                "SELECT COUNT(*) FROM memory_learning_candidates WHERE first_observed_at >= ?",
                (cutoff,),
            ).fetchone()[0]
        )
        promoted = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM memory_learning_candidates c
                JOIN memories m ON m.id = c.memory_id
                WHERE c.first_observed_at >= ? AND m.status = 'active'
                """,
                (cutoff,),
            ).fetchone()[0]
        )
        return MemoryLearningQuality(
            candidates=candidates,
            promoted=promoted,
            promotion_rate=round(promoted / candidates, 3) if candidates else None,
        )


def _breakdown(counts: dict[str, int], *, positive: tuple[str, ...]) -> OutcomeBreakdown:
    total = sum(counts.values())
    return OutcomeBreakdown(
        total=total,
        counts=counts,
        positive_rate=(
            round(sum(counts.get(name, 0) for name in positive) / total, 3) if total else None
        ),
    )
