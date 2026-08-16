from __future__ import annotations

from datetime import UTC, datetime, time
from pathlib import Path

import pytest

from alfred.config import Settings
from alfred.db import Database
from alfred.outbox import Outbox
from alfred.quiet_hours import QuietHours
from alfred.telegram_runtime import TelegramOutboxWorker


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, dict | None]] = []

    def send_message(self, *, chat_id: int, text: str, reply_markup: dict | None = None) -> int:
        self.sent.append((chat_id, text, reply_markup))
        return 1


def test_overnight_window_is_active_across_midnight() -> None:
    hours = QuietHours(start=time(22, 0), end=time(7, 0), timezone="UTC")
    assert hours.is_active(datetime(2026, 8, 16, 23, 0, tzinfo=UTC))
    assert hours.is_active(datetime(2026, 8, 17, 6, 59, tzinfo=UTC))
    assert not hours.is_active(datetime(2026, 8, 17, 7, 0, tzinfo=UTC))
    assert not hours.is_active(datetime(2026, 8, 16, 21, 59, tzinfo=UTC))


def test_same_day_window() -> None:
    hours = QuietHours(start=time(12, 0), end=time(14, 0), timezone="UTC")
    assert hours.is_active(datetime(2026, 8, 16, 12, 0, tzinfo=UTC))
    assert hours.is_active(datetime(2026, 8, 16, 13, 30, tzinfo=UTC))
    assert not hours.is_active(datetime(2026, 8, 16, 14, 0, tzinfo=UTC))
    assert not hours.is_active(datetime(2026, 8, 16, 11, 59, tzinfo=UTC))


def test_disabled_when_unset() -> None:
    assert not QuietHours.disabled().is_active(datetime(2026, 8, 16, 3, 0, tzinfo=UTC))
    assert not QuietHours.from_environment().is_active(datetime(2026, 8, 16, 3, 0, tzinfo=UTC))


def test_from_environment_reads_start_end_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_QUIET_HOURS_START", "22:00")
    monkeypatch.setenv("ALFRED_QUIET_HOURS_END", "07:00")
    monkeypatch.setenv("ALFRED_QUIET_HOURS_TIMEZONE", "America/New_York")
    hours = QuietHours.from_environment()
    assert hours.start == time(22, 0)
    assert hours.end == time(7, 0)
    assert hours.timezone == "America/New_York"
    settings = Settings.from_environment()
    assert settings.quiet_hours.start == time(22, 0)


def test_partial_env_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_QUIET_HOURS_START", "22:00")
    monkeypatch.delenv("ALFRED_QUIET_HOURS_END", raising=False)
    with pytest.raises(ValueError, match="both start and end"):
        QuietHours.from_environment()


def _seed_job(database: Database) -> str:
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                INSERT INTO jobs (id, kind, schedule_json, next_run_at, payload_json, state, idempotency_key)
                VALUES ('job-quiet', 'reminder', '{}', '2026-01-01T00:00:00+00:00', '{}', 'active', 'quiet-test-job')
                """
            )
    return "job-quiet"


def test_quiet_hours_hold_job_deliveries_but_not_interactive_replies(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    job_id = _seed_job(database)
    with database.connect() as connection:
        with database.transaction(connection):
            Outbox.enqueue(
                connection,
                destination="telegram:20",
                payload={"text": "Reminder: take meds"},
                idempotency_key="job-delivery:1",
                job_id=job_id,
            )
            Outbox.enqueue(
                connection,
                destination="telegram:20",
                payload={"text": "here's your answer"},
                idempotency_key="hermes-reply:1:0",
            )
    fake = FakeTelegram()
    hours = QuietHours(start=time(22, 0), end=time(7, 0), timezone="UTC")
    worker = TelegramOutboxWorker(database, fake, {20}, quiet_hours=hours)

    results = worker.deliver_pending(now=datetime(2026, 8, 16, 23, 30, tzinfo=UTC))

    assert [item.state for item in results] == ["sent"]
    assert fake.sent == [(20, "here's your answer", None)]
    with database.connect() as connection:
        states = {
            row["idempotency_key"]: row["state"]
            for row in connection.execute(
                "SELECT idempotency_key, state FROM outbox ORDER BY created_at, rowid"
            )
        }
    assert states["hermes-reply:1:0"] == "sent"
    assert states["job-delivery:1"] == "pending"

    # After quiet hours, the held reminder delivers.
    more = worker.deliver_pending(now=datetime(2026, 8, 17, 7, 0, tzinfo=UTC))
    assert [item.state for item in more] == ["sent"]
    assert fake.sent[-1] == (20, "Reminder: take meds", None)
