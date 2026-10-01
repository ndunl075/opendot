from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opendot_core.db import Database
from opendot_core.runtime_control import (
    DEFAULT_STALE_SECONDS,
    format_runtime_status,
    paired_chat_ids_from_run_args,
    record_heartbeat,
    request_restart,
    restart_daemon,
    restart_pending,
    runtime_status,
    watchdog_check,
)


def test_record_heartbeat_and_runtime_status(tmp_path: Path) -> None:
    database = Database(tmp_path / "opendot.db")
    database.migrate()
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)

    record_heartbeat(database, now=now)
    status = runtime_status(database, now=now + timedelta(seconds=30))

    assert status.healthy is True
    assert status.seconds_since_cycle == pytest.approx(30.0)
    assert "running" in format_runtime_status(status)


def test_runtime_status_marks_stale_heartbeat(tmp_path: Path) -> None:
    database = Database(tmp_path / "opendot.db")
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
    database = Database(tmp_path / "opendot.db")
    database.migrate()

    request_restart(database)

    assert restart_pending(database) is True


def test_paired_chat_ids_from_run_args() -> None:
    args = [
        "run",
        "--pair",
        "4242424242:4242424242",
        "--chat-id",
        "4242424242",
    ]
    assert paired_chat_ids_from_run_args(args) == {4242424242}


def test_watchdog_check_restarts_when_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = Database(tmp_path / "opendot.db")
    database.migrate()
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    record_heartbeat(database, now=now - timedelta(seconds=600))
    restart = MagicMock(return_value=type("RestartResult", (), {"ok": True, "method": "restart_command", "detail": "systemctl --user restart opendot"})())

    monkeypatch.setattr("opendot_core.runtime_control.restart_daemon", restart)

    result = watchdog_check(database, stale_seconds=300, auto_restart=True, now=now)

    assert result.was_stale is True
    assert result.action == "restart_command"
    restart.assert_called_once()


def test_watchdog_check_skips_fresh_heartbeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = Database(tmp_path / "opendot.db")
    database.migrate()
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    record_heartbeat(database, now=now)
    restart = MagicMock()

    monkeypatch.setattr("opendot_core.runtime_control.restart_daemon", restart)

    result = watchdog_check(database, stale_seconds=300, auto_restart=True, now=now)

    assert result.was_stale is False
    restart.assert_not_called()


def test_restart_daemon_needs_an_operator_supplied_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENDOT_RESTART_COMMAND", raising=False)

    result = restart_daemon()

    assert result.ok is False
    assert result.method == "none"
    assert "OPENDOT_RESTART_COMMAND" in result.detail


def test_restart_daemon_runs_the_configured_command_without_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENDOT_RESTART_COMMAND", "systemctl --user restart opendot")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    result = restart_daemon(runner=fake_run)

    assert result.ok is True
    assert calls == [["systemctl", "--user", "restart", "opendot"]]


def test_restart_daemon_reports_a_failing_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENDOT_RESTART_COMMAND", "false")

    def fake_run(argv, **kwargs):
        return type("Completed", (), {"returncode": 3, "stdout": "", "stderr": "unit not found"})()

    result = restart_daemon(runner=fake_run)

    assert result.ok is False
    assert result.detail == "unit not found"

