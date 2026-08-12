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

Install with ``--username``/``--password`` (see README's "As a real Windows
service" section), not the bare ``install`` verb. Every connector credential
this process reads via ``SystemKeyringSecretStore`` lives in the operator's
own Windows account's DPAPI-protected Credential Manager; the bare ``install``
verb's default account, ``LocalSystem``, is a distinct, secretless security
context that can never decrypt them. Installing without an explicit account
still reports success and the service still starts -- it just dies
immediately afterward with ``missing local credential-store secret: ...``,
which reads like a missing ``keyring set`` rather than the actual, unrelated
cause.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Type checkers always see the real pywin32 modules, so annotations and the
    # class body below are checked against their actual attributes rather than
    # ``None``. The runtime import below is the one that may legitimately fail.
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
else:
    try:
        import servicemanager
        import win32event
        import win32service
        import win32serviceutil
    except ImportError:  # pragma: no cover - exercised only where pywin32 is absent
        servicemanager = win32event = win32service = win32serviceutil = None

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


def _repo_root() -> Path:
    """Locate the alfred-core checkout from this installed module's own location.

    `alfred run`, `alfred service-configure`, and `alfred init` all resolve
    `.alfred/` (and every other cwd-relative default, like the database path)
    against whichever directory the operator invoked them from -- normally the
    repository root, per README's documented usage. `SvcDoRun` chdir()s here
    first so the Windows service resolves those same defaults the same way,
    since the Service Control Manager instead launches `pythonservice.exe` with
    its own working directory (typically %SystemRoot%\\System32) -- not this
    repository -- and this project is only ever installed editable (see
    `scripts/install.ps1`), so `winservice.py`'s own path is always inside the
    checkout it should chdir into.
    """
    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / "pyproject.toml").exists():
        raise RuntimeError(
            f"could not locate the alfred-core repository root from {__file__} "
            f"(expected {repo_root / 'pyproject.toml'} to exist); the Windows service "
            "currently requires an editable install (pip install -e '.[dev]')"
        )
    return repo_root


if win32serviceutil is not None:

    class AlfredWindowsService(win32serviceutil.ServiceFramework):
        """Defined at module level, not inside a function.

        When the Service Control Manager actually starts the service, a fresh
        ``pythonservice.exe`` process imports ``alfred.winservice`` and looks up this
        class by name using the module/class string it stored in the registry at
        install time -- it does not reuse any object built during ``alfred-service
        install``. A class that only exists as a local variable inside a function is
        never a real attribute of the module, so that lookup fails with
        ``AttributeError: module 'alfred.winservice' has no attribute
        'AlfredWindowsService'`` and the service terminates immediately (Windows
        exit code 1066, "Incorrect function") even though installation itself
        reported success.
        """

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
            # Deferred, not a module-level import: `alfred.cli` imports
            # `alfred.winservice` (for `service-configure`), so importing `.cli`
            # back at this module's top level would be a circular import. By the
            # time the service actually starts running, both modules have long
            # since finished initializing, so the import here is safe.
            from .cli import build_parser, database_from_args, running_alfred_runner

            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE, servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, "")
            )
            try:
                os.chdir(_repo_root())
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


def _build_service_class() -> Any:
    """Return the pywin32 service class, importing pywin32 lazily.

    Importing win32serviceutil/win32service/win32event/servicemanager at module
    import time would make ``alfred.winservice`` unimportable anywhere pywin32
    isn't installed (non-Windows dev, or a stripped-down environment); the
    module-level ``try``/``except ImportError`` above keeps that failure confined
    to actually trying to use the service, while still leaving
    ``AlfredWindowsService`` as a real module attribute pywin32 can find by name
    once it is installed.
    """
    if win32serviceutil is None:
        raise ImportError("pywin32 is required to build or run the Alfred Windows service")
    return AlfredWindowsService


def main() -> None:
    """Handle the standard pywin32 service verbs: install, remove, start, stop, restart, debug."""
    win32serviceutil.HandleCommandLine(_build_service_class())


if __name__ == "__main__":
    main()
