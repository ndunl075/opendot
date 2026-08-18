from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from alfred.db import Database
from alfred.runtime_control import (
    DEFAULT_STALE_SECONDS,
    format_runtime_status,
    paired_chat_ids_from_run_args,
    record_heartbeat,
    request_restart,
    restart_pending,
    runtime_status,
    watchdog_check,
)


def test_record_heartbeat_and_runtime_status(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)

    record_heartbeat(database, now=now)
    status = runtime_status(database, now=now + timedelta(seconds=30))

    assert status.healthy is True
    assert status.seconds_since_cycle == pytest.approx(30.0)
    assert "running" in format_runtime_status(status)


def test_runtime_status_marks_stale_heartbeat(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    record_heartbeat(database, now=now)

    status = runtime_status(
        database,
        stale_seconds=DEFAULT_STALE_SECONDS,
        now=now + timedelta(seconds=DEFAULT_STALE_SECONDS + 1),
    )

    assert status.healthy is False
    assert "stalled or stopped" in format_runtime_status(status)


def test_restart_request_is_visible_to_runner(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()

    request_restart(database)

    assert restart_pending(database) is True


def test_paired_chat_ids_from_run_args() -> None:
    args = [
        "run",
        "--pair",
        "7952089798:7952089798",
        "--chat-id",
        "7952089798",
    ]
    assert paired_chat_ids_from_run_args(args) == {7952089798}


def test_watchdog_check_restarts_when_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    record_heartbeat(database, now=now - timedelta(seconds=600))
    restart = MagicMock(return_value=type("RestartResult", (), {"ok": True, "method": "scheduled_task", "detail": "AlfredRestart"})())

    monkeypatch.setattr("alfred.runtime_control.restart_alfred", restart)
    monkeypatch.setattr("alfred.runtime_control.windows_service_state", lambda: "running")

    result = watchdog_check(database, stale_seconds=300, auto_restart=True, now=now)

    assert result.was_stale is True
    assert result.action == "scheduled_task"
    restart.assert_called_once()


def test_watchdog_check_skips_fresh_heartbeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    record_heartbeat(database, now=now)
    restart = MagicMock()

    monkeypatch.setattr("alfred.runtime_control.restart_alfred", restart)

    result = watchdog_check(database, stale_seconds=300, auto_restart=True, now=now)

    assert result.was_stale is False
    restart.assert_not_called()
