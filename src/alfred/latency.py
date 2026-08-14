"""Operator-readable Telegram-to-Hermes latency telemetry."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime

from pydantic import BaseModel

from .db import Database


class LatencyPercentiles(BaseModel):
    count: int
    p50_ms: int | None = None
    p95_ms: int | None = None


class LatencySample(BaseModel):
    update_id: str
    outcome: str
    runtime: str
    tool_count: int | None = None
    received_at: datetime | None = None
    ack_ms: int | None = None
    context_ms: int
    agent_ms: int
    response_ready_ms: int
    delivered_ms: int | None = None


class LatencyReport(BaseModel):
    generated_at: datetime
    instrumented_turns: int
    delivered_turns: int
    acknowledgement: LatencyPercentiles
    context: LatencyPercentiles
    agent: LatencyPercentiles
    response_ready: LatencyPercentiles
    delivered: LatencyPercentiles
    recent: list[LatencySample]


class LatencyService:
    """Build a content-free latency report from existing events, audit, and outbox rows."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def report(self, *, limit: int = 20) -> LatencyReport:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.database.migrate()
        with self.database.connect() as connection:
            audit_rows = connection.execute(
                """
                SELECT occurred_at, outcome, result_json
                FROM tool_runs
                WHERE tool = 'hermes_bridge'
                ORDER BY sequence DESC
                LIMIT ?
                """,
                (limit * 4,),
            ).fetchall()

            samples: list[LatencySample] = []
            for row in audit_rows:
                payload = json.loads(row["result_json"])
                if payload.get("timing_version") != 1:
                    continue
                update_id = str(payload.get("update_id") or "")
                if not update_id:
                    continue
                event = connection.execute(
                    "SELECT occurred_at FROM events WHERE source = 'telegram' AND external_id = ?",
                    (update_id,),
                ).fetchone()
                acknowledgement = connection.execute(
                    "SELECT sent_at FROM outbox WHERE idempotency_key = ?",
                    (f"telegram-receipt:{update_id}",),
                ).fetchone()
                reply = connection.execute(
                    "SELECT sent_at FROM outbox WHERE idempotency_key = ?",
                    (f"hermes-reply:{update_id}:0",),
                ).fetchone()
                received_at = _parse_datetime(event["occurred_at"]) if event else None
                ack_at = _parse_datetime(acknowledgement["sent_at"]) if acknowledgement else None
                reply_at = _parse_datetime(reply["sent_at"]) if reply else None
                samples.append(
                    LatencySample(
                        update_id=update_id,
                        outcome=str(row["outcome"]),
                        runtime=str(payload.get("runtime") or "unknown"),
                        tool_count=_optional_int(payload.get("tool_count")),
                        received_at=received_at,
                        ack_ms=_elapsed_ms(received_at, ack_at),
                        context_ms=_required_int(payload.get("context_ms")),
                        agent_ms=_required_int(payload.get("agent_ms")),
                        response_ready_ms=_required_int(payload.get("response_ready_ms")),
                        delivered_ms=_elapsed_ms(received_at, reply_at),
                    )
                )
                if len(samples) >= limit:
                    break

        return LatencyReport(
            generated_at=datetime.now(UTC),
            instrumented_turns=len(samples),
            delivered_turns=sum(sample.delivered_ms is not None for sample in samples),
            acknowledgement=_percentiles(sample.ack_ms for sample in samples),
            context=_percentiles(sample.context_ms for sample in samples),
            agent=_percentiles(sample.agent_ms for sample in samples),
            response_ready=_percentiles(sample.response_ready_ms for sample in samples),
            delivered=_percentiles(sample.delivered_ms for sample in samples),
            recent=samples,
        )


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _elapsed_ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(0, round((end - start).total_seconds() * 1000))


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)


def _required_int(value: object) -> int:
    return max(0, int(value or 0))


def _percentiles(values) -> LatencyPercentiles:
    ordered = sorted(int(value) for value in values if value is not None)
    if not ordered:
        return LatencyPercentiles(count=0)

    def nearest_rank(percent: float) -> int:
        index = max(0, math.ceil(percent * len(ordered)) - 1)
        return ordered[index]

    return LatencyPercentiles(
        count=len(ordered),
        p50_ms=nearest_rank(0.50),
        p95_ms=nearest_rank(0.95),
    )
