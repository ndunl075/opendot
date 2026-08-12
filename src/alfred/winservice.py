"""Optional Windows service wrapper for `alfred run`.

Section 6 flags packaging the always-on loop as a real OS service as
unbuilt: today `alfred run` depends on an external keep-alive (a
login-triggered Task Scheduler entry -- see `scripts/install.ps1
-RegisterScheduledTask` -- or a terminal left open). This is the real
service alternative: install once as Administrator, and Alfred survives
logoff/reboot with no logged-in session at all, using Windows' own service
recovery options (`sc.exe failure`) for restart-on-crash instead of an
ad-hoc supervisor.

This does not reimplement `alfred run`'s construction logic. It drives the
exact same `running_alfred_runner()` context manager the CLI uses, built
from arguments parsed by the exact same `build_parser()` -- so the service
and the CLI can never quietly diverge in what they actually run. Only how
the loop is told to stop differs: `alfred run` catches KeyboardInterrupt;
the service passes AlfredRunner.run_forever() a `stop_check` that watches
the Win32 event SvcStop() signals.

Requires pywin32, already an indirect dependency of the `mcp` package on
Windows. Installing, starting, stopping, and removing the service are all
Administrator actions performed by running this module directly -- Alfred
itself never elevates or registers a service on its own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CONFIG_FILENAME = "service.json"
_DEFAULT_ALFRED_DIR = Path(".alfred")


def configure(run_args: list[str], *, alfred_dir: Path = _DEFAULT_ALFRED_DIR) -> Path:
    """Store the exact ``alfred`` arguments (starting with ``run``) the service will launch.

    Does not touch a running service -- run ``python -m alfred.winservice
    restart`` after changing this to pick it up.
    """
    if not run_args or run_args[0] != "run":
        raise ValueError("run_args must start with 'run', e.g. ['run', '--pair', '123:456', '--chat-id', '123']")
    alfred_dir.mkdir(parents=True, exist_ok=True)
    config_path = alfred_dir / _CONFIG_FILENAME
    config_path.write_text(json.dumps({"args": run_args}, indent=2), encoding="utf-8")
    return config_path


def load_configured_args(*, alfred_dir: Path = _DEFAULT_ALFRED_DIR) -> list[str]:
    """Read back the arguments a prior ``configure()`` call stored."""
    config_path = alfred_dir / _CONFIG_FILENAME
    if not config_path.exists():
        raise RuntimeError(
            f"{config_path} does not exist; run 'alfred service-configure run ...' before starting the service"
        )
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{config_path} is not valid JSON: {error}") from error
    run_args = data.get("args") if isinstance(data, dict) else None
    if not isinstance(run_args, list) or not all(isinstance(item, str) for item in run_args):
        raise RuntimeError(f"{config_path} is malformed: 'args' must be a list of strings")
    return run_args


def _build_service_class() -> Any:
    """Import pywin32 and define the service class lazily.

    Importing win32serviceutil/win32service/win32event/servicemanager at
    module import time would make ``alfred.winservice`` unimportable
    anywhere pywin32 isn't installed (non-Windows dev, or a stripped-down
    environment); nothing else in this codebase imports this module, so
    deferring the import here keeps that failure confined to actually
    trying to use the service.
    """
    import win32event
    import win32service
    import win32serviceutil
    import servicemanager

    from .cli import build_parser, database_from_args, running_alfred_runner

    class AlfredWindowsService(win32serviceutil.ServiceFramework):
        _svc_name_ = "Alfred"
        _svc_display_name_ = "Alfred Personal Secretary"
        _svc_description_ = (
            "Runs Alfred's always-on loop (Telegram/Slack intake and delivery, due jobs, "
            "connector sync) as a background service, independent of any logged-in session. "
            "Configure its arguments with 'alfred service-configure' before starting."
        )

        def __init__(self, args: Any) -> None:
            super().__init__(args)
            self._stop_event = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self._stop_event)

        def _stop_requested(self) -> bool:
            return win32event.WaitForSingleObject(self._stop_event, 0) == win32event.WAIT_OBJECT_0

        def SvcDoRun(self) -> None:
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE, servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, "")
            )
            try:
                run_args = load_configured_args()
                parsed = build_parser().parse_args(run_args)
                database = database_from_args(parsed)
            except Exception as error:
                servicemanager.LogErrorMsg(f"Alfred service failed to start: {error}")
                raise
            with running_alfred_runner(database, parsed) as runner:
                runner.run_forever(stop_check=self._stop_requested)
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE, servicemanager.PYS_SERVICE_STOPPED, (self._svc_name_, "")
            )

    return AlfredWindowsService


def main() -> None:
    """Handle the standard pywin32 service verbs: install, remove, start, stop, restart, debug."""
    import win32serviceutil

    win32serviceutil.HandleCommandLine(_build_service_class())


if __name__ == "__main__":
    main()
