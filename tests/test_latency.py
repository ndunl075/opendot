from datetime import UTC, datetime, timedelta
from pathlib import Path

from alfred.audit import AuditEvent, AuditLog
from alfred.db import Database
from alfred.hermes_bridge import AgentRunResult, HermesBridge
from alfred.latency import LatencyService, _percentiles
from alfred.telegram import TelegramGateway, TelegramPair, TelegramUpdate


def _defer(database: Database, update_id: int) -> None:
    TelegramGateway(
        database,
        {TelegramPair(chat_id=20, user_id=10)},
        defer_unparsed_to_agent=True,
    ).handle(
        TelegramUpdate.model_validate(
            {
                "update_id": update_id,
                "message": {
                    "message_id": update_id + 100,
                    "date": int(datetime.now(UTC).timestamp()),
                    "chat": {"id": 20},
                    "from": {"id": 10},
                    "text": "summarize my inbox",
                },
            }
        )
    )


def test_latency_report_correlates_receipt_bridge_and_delivered_reply(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _defer(database, 42)
    ticks = iter((0.0, 0.1, 0.12, 0.2, 1.2, 1.3))
    agent = lambda prompt: AgentRunResult(  # noqa: E731
        text="one message matters.",
        ok=True,
        duration_ms=980,
        runtime="oneshot",
        tool_count=0,
    )

    HermesBridge(database, agent, monotonic=lambda: next(ticks)).run_once()

    with database.connect() as connection:
        received = datetime.fromisoformat(
            connection.execute(
                "SELECT occurred_at FROM events WHERE external_id = '42'"
            ).fetchone()["occurred_at"]
        )
        if received.tzinfo is None:
            received = received.replace(tzinfo=UTC)
        with database.transaction(connection):
            connection.execute(
                "UPDATE outbox SET state = 'sent', sent_at = ? WHERE idempotency_key = ?",
                ((received + timedelta(milliseconds=500)).isoformat(), "telegram-receipt:42"),
            )
            connection.execute(
                "UPDATE outbox SET state = 'sent', sent_at = ? WHERE idempotency_key = ?",
                ((received + timedelta(milliseconds=1500)).isoformat(), "hermes-reply:42:0"),
            )

    report = LatencyService(database).report()

    assert (report.instrumented_turns, report.delivered_turns) == (1, 1)
    assert report.acknowledgement.p50_ms == 500
    assert report.delivered.p95_ms == 1500
    sample = report.recent[0]
    assert (sample.context_ms, sample.agent_ms, sample.response_ready_ms) == (20, 1000, 1300)
    assert (sample.runtime, sample.tool_count, sample.outcome) == ("oneshot", 0, "ok")


def test_latency_report_ignores_old_uninstrumented_bridge_rows(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    AuditLog(database).append(
        AuditEvent(
            actor="system",
            client="test",
            tool="hermes_bridge",
            outcome="ok",
            result={"update_id": "1"},
        )
    )

    report = LatencyService(database).report()

    assert report.instrumented_turns == 0
    assert report.recent == []


def test_latency_percentiles_use_nearest_rank() -> None:
    summary = _percentiles([100, 200, 300, 400, None])

    assert summary.model_dump() == {"count": 4, "p50_ms": 200, "p95_ms": 400}
