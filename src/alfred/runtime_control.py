"""Runtime liveness, restart, and watchdog helpers for Alfred's always-on loop.

Telegram `/status` and `/restart` work while the loop is alive. When it is
dead or hung, a short Task Scheduler job runs `alfred watchdog-check`, which
reads the heartbeat written each cycle, restarts the Windows service (or
falls back to the configured `alfred run` command), and does a one-shot
Telegram poll for `/wake` or `/restart` so the owner can nudge it from a
phone without waiting for the next automatic check.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from .db import Database
from .winservice import _DEFAULT_ALFRED_DIR, load_configured_args

RUNTIME_CONNECTOR = "runtime"
RUNTIME_ACCOUNT = "runner"
DEFAULT_STALE_SECONDS = 300.0
RESTART_TASK_NAME = "AlfredRestart"
WATCHDOG_TASK_NAME = "AlfredWatchdog"
_SERVICE_NAME = "Alfred"
_PAIR_PATTERN = re.compile(r"^(\d+):(\d+)$")


class RuntimeStatus(BaseModel):
    last_cycle_at: datetime | None
    seconds_since_cycle: float | None
    healthy: bool
    service_state: str | None
    restart_requested_at: datetime | None


class WatchdogResult(BaseModel):
    was_stale: bool
    action: str
    detail: str
    telegram_rescue: list[str] = []


class RestartResult(BaseModel):
    ok: bool
    method: str
    detail: str


def record_heartbeat(database: Database, *, now: datetime | None = None) -> None:
    """Mark one completed runner cycle."""
    timestamp = (now or datetime.now(UTC)).isoformat()
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at)
                VALUES (?, ?, NULL, ?, NULL, ?)
                ON CONFLICT(connector, account) DO UPDATE SET
                    last_success_at = excluded.last_success_at,
                    last_error = NULL,
                    updated_at = excluded.updated_at
                """,
                (RUNTIME_CONNECTOR, RUNTIME_ACCOUNT, timestamp, timestamp),
            )


def request_restart(database: Database, *, now: datetime | None = None) -> datetime:
    """Queue a restart for the next runner cycle."""
    requested_at = now or datetime.now(UTC)
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            _write_restart_request(connection, requested_at=requested_at)
    return requested_at


def _write_restart_request(connection, *, requested_at: datetime) -> None:
    timestamp = requested_at.isoformat()
    connection.execute(
        """
        INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at)
        VALUES (?, ?, ?, NULL, NULL, ?)
        ON CONFLICT(connector, account) DO UPDATE SET
            cursor = excluded.cursor,
            updated_at = excluded.updated_at
        """,
        (RUNTIME_CONNECTOR, RUNTIME_ACCOUNT, timestamp, timestamp),
    )


def clear_restart_request(database: Database) -> None:
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                UPDATE sync_state
                SET cursor = NULL, updated_at = ?
                WHERE connector = ? AND account = ?
                """,
                (datetime.now(UTC).isoformat(), RUNTIME_CONNECTOR, RUNTIME_ACCOUNT),
            )


def restart_pending(database: Database, *, now: datetime | None = None) -> bool:
    status = runtime_status(database, now=now)
    return status.restart_requested_at is not None


def runtime_status(
    database: Database,
    *,
    stale_seconds: float = DEFAULT_STALE_SECONDS,
    now: datetime | None = None,
) -> RuntimeStatus:
    current = now or datetime.now(UTC)
    database.migrate()
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT cursor, last_success_at FROM sync_state
            WHERE connector = ? AND account = ?
            """,
            (RUNTIME_CONNECTOR, RUNTIME_ACCOUNT),
        ).fetchone()
    restart_requested_at: datetime | None = None
    last_cycle_at: datetime | None = None
    if row is not None:
        if row["cursor"]:
            restart_requested_at = datetime.fromisoformat(str(row["cursor"]))
        if row["last_success_at"]:
            last_cycle_at = datetime.fromisoformat(str(row["last_success_at"]))
    seconds_since = None
    if last_cycle_at is not None:
        seconds_since = max(0.0, (current - last_cycle_at.astimezone(UTC)).total_seconds())
    healthy = seconds_since is not None and seconds_since <= stale_seconds
    return RuntimeStatus(
        last_cycle_at=last_cycle_at,
        seconds_since_cycle=seconds_since,
        healthy=healthy,
        service_state=windows_service_state(),
        restart_requested_at=restart_requested_at,
    )


def format_runtime_status(status: RuntimeStatus) -> str:
    if status.healthy:
        age = int(status.seconds_since_cycle or 0)
        line = f"alfred is running. last cycle {age}s ago."
    elif status.last_cycle_at is None:
        line = "alfred has no heartbeat yet."
    else:
        age = int(status.seconds_since_cycle or 0)
        line = f"alfred looks stalled or stopped. last cycle {age}s ago."
    if status.service_state:
        line += f" windows service: {status.service_state}."
    if status.restart_requested_at is not None:
        line += " restart is queued."
    return line


def windows_service_state() -> str | None:
    if platform.system() != "Windows":
        return None
    try:
        completed = subprocess.run(
            ["sc.exe", "query", _SERVICE_NAME],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    if completed.returncode != 0:
        return "not_installed"
    output = completed.stdout.upper()
    if "RUNNING" in output:
        return "running"
    if "STOPPED" in output:
        return "stopped"
    if "START_PENDING" in output:
        return "starting"
    if "STOP_PENDING" in output:
        return "stopping"
    return "unknown"


def restart_alfred(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> RestartResult:
    """Try to restart Alfred through the registered restart task or Windows service."""
    if platform.system() == "Windows":
        task = runner(
            ["schtasks.exe", "/Run", "/TN", RESTART_TASK_NAME],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if task.returncode == 0:
            return RestartResult(ok=True, method="scheduled_task", detail=RESTART_TASK_NAME)
        service_state = windows_service_state()
        if service_state == "running":
            stop = runner(["sc.exe", "stop", _SERVICE_NAME], capture_output=True, text=True, check=False, timeout=60)
            if stop.returncode != 0:
                return RestartResult(
                    ok=False,
                    method="service_stop",
                    detail=(stop.stderr or stop.stdout or "stop failed").strip(),
                )
        if service_state in {"running", "stopped", "starting", "stopping"}:
            start = runner(["sc.exe", "start", _SERVICE_NAME], capture_output=True, text=True, check=False, timeout=60)
            if start.returncode == 0:
                return RestartResult(ok=True, method="service_restart", detail=_SERVICE_NAME)
            return RestartResult(
                ok=False,
                method="service_start",
                detail=(start.stderr or start.stdout or "start failed").strip(),
            )
    return RestartResult(ok=False, method="none", detail="no restart path available")


def start_configured_runner(
    *,
    alfred_dir: Path = _DEFAULT_ALFRED_DIR,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> RestartResult:
    """Start `alfred run` from service.json when no Windows service is installed."""
    try:
        run_args = load_configured_args(alfred_dir=alfred_dir)
    except RuntimeError as error:
        return RestartResult(ok=False, method="configured_run", detail=str(error))
    repo_root = Path(__file__).resolve().parents[2]
    command = [sys.executable, "-m", "alfred.cli", *run_args]
    creationflags = 0
    if platform.system() == "Windows":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
    try:
        subprocess.Popen(
            command,
            cwd=repo_root,
            creationflags=creationflags,
            close_fds=True,
        )
    except OSError as error:
        return RestartResult(ok=False, method="configured_run", detail=str(error))
    return RestartResult(ok=True, method="configured_run", detail=" ".join(run_args))


def watchdog_check(
    database: Database,
    *,
    stale_seconds: float = DEFAULT_STALE_SECONDS,
    auto_restart: bool = True,
    paired_chat_ids: set[int] | None = None,
    telegram_token: str | None = None,
    now: datetime | None = None,
) -> WatchdogResult:
    """Restart a stale runner and optionally rescue Telegram `/wake` commands."""
    status = runtime_status(database, stale_seconds=stale_seconds, now=now)
    rescue_messages: list[str] = []
    if status.healthy:
        return WatchdogResult(
            was_stale=False,
            action="none",
            detail="heartbeat is fresh",
            telegram_rescue=rescue_messages,
        )

    action = "none"
    detail = "heartbeat is stale"
    if auto_restart:
        restart = restart_alfred()
        if restart.ok:
            action = restart.method
            detail = restart.detail
        elif windows_service_state() in {None, "not_installed", "unavailable"}:
            fallback = start_configured_runner()
            action = fallback.method if fallback.ok else "failed"
            detail = fallback.detail
        else:
            action = "failed"
            detail = restart.detail

    if telegram_token and paired_chat_ids:
        rescue_messages = _telegram_rescue_poll(
            database,
            token=telegram_token,
            allowed_chat_ids=paired_chat_ids,
        )
        for message in rescue_messages:
            if message.startswith("/restart") or message.startswith("/wake"):
                forced = restart_alfred()
                if forced.ok:
                    action = forced.method
                    detail = forced.detail
                elif windows_service_state() in {None, "not_installed", "unavailable"}:
                    fallback = start_configured_runner()
                    action = fallback.method if fallback.ok else action
                    detail = fallback.detail if fallback.ok else detail
                break

    return WatchdogResult(
        was_stale=True,
        action=action,
        detail=detail,
        telegram_rescue=rescue_messages,
    )


def paired_chat_ids_from_run_args(run_args: list[str]) -> set[int]:
    chat_ids: set[int] = set()
    iterator = iter(run_args)
    for token in iterator:
        if token == "--chat-id":
            value = next(iterator, None)
            if value is not None:
                chat_ids.add(int(value))
        elif token == "--pair":
            value = next(iterator, None)
            if value is not None and _PAIR_PATTERN.fullmatch(value):
                chat_ids.add(int(value.split(":", 1)[0]))
    return chat_ids


def paired_chat_ids_from_config(*, alfred_dir: Path = _DEFAULT_ALFRED_DIR) -> set[int]:
    try:
        return paired_chat_ids_from_run_args(load_configured_args(alfred_dir=alfred_dir))
    except RuntimeError:
        return set()


def _telegram_rescue_poll(
    database: Database,
    *,
    token: str,
    allowed_chat_ids: set[int],
) -> list[str]:
    """One-shot Telegram poll while Alfred is down. Returns matched command texts."""
    from .telegram import TelegramUpdate
    from .telegram_bot import TelegramBotClient

    with database.connect() as connection:
        row = connection.execute(
            "SELECT cursor FROM sync_state WHERE connector = 'telegram' AND account = 'bot'"
        ).fetchone()
    offset = int(row["cursor"]) + 1 if row and row["cursor"] is not None else None

    client = TelegramBotClient(token)
    matched: list[str] = []
    latest_cursor: int | None = None
    try:
        updates = client.get_updates(offset=offset, timeout_seconds=0)
        for raw in updates:
            update_id = int(raw.get("update_id", 0))
            latest_cursor = update_id if latest_cursor is None else max(latest_cursor, update_id)
            update = TelegramUpdate.model_validate(raw)
            message = update.message
            if message is None or not message.text:
                continue
            text = message.text.strip()
            lowered = text.casefold()
            command = lowered.split()[0]
            if command not in {"/status", "/restart", "/wake"}:
                continue
            if message.chat.id not in allowed_chat_ids:
                continue
            matched.append(command)
            if command == "/status":
                reply = format_runtime_status(runtime_status(database))
            else:
                request_restart(database)
                reply = "restart queued. watchdog is waking alfred now."
            client.send_message(chat_id=message.chat.id, text=reply)
        if latest_cursor is not None:
            now = datetime.now(UTC).isoformat()
            with database.connect() as connection:
                with database.transaction(connection):
                    connection.execute(
                        """
                        INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at)
                        VALUES ('telegram', 'bot', ?, ?, NULL, ?)
                        ON CONFLICT(connector, account) DO UPDATE SET
                            cursor = excluded.cursor,
                            last_success_at = excluded.last_success_at,
                            last_error = NULL,
                            updated_at = excluded.updated_at
                        """,
                        (str(latest_cursor), now, now),
                    )
    finally:
        client.close()
    return matched


def note_watchdog_result(database: Database, result: WatchdogResult) -> None:
    timestamp = datetime.now(UTC).isoformat()
    detail = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                """
                INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at)
                VALUES ('runtime', 'watchdog', NULL, ?, ?, ?)
                ON CONFLICT(connector, account) DO UPDATE SET
                    last_success_at = excluded.last_success_at,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (timestamp, detail[:500], timestamp),
            )
