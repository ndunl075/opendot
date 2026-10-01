"""`opendot service install|uninstall|status` (M4 task 4.1, ARCHITECTURE.md section 11).

One long-lived process (`opendot serve`) gives the API, the UI and the always-on loop, so the
service runs exactly that. Per OS:

- macOS: a launchd user agent (``~/Library/LaunchAgents``), restarted when it exits badly.
- Windows: a Task Scheduler "at logon" task for the current user (``schtasks``), restarted on failure.
- Linux: a systemd user unit (``~/.config/systemd/user``), ``Restart=on-failure``.

Nothing here needs sudo or admin: every definition lives in the user's own folders. The
definition generators (``launchd_plist``, ``systemd_unit``, ``task_xml``) are pure functions
(string in, string out); only :class:`ServiceManager` touches the OS, and it does so through an
injected ``run`` so tests never shell out.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any, Literal, Protocol
from xml.sax.saxutils import escape

APP_IDENTIFIER = "org.opendot.desktop"  # same folder the desktop app uses, so both share one database
SERVICE_LABEL = "org.opendot.daemon"  # launchd label
UNIT_NAME = "opendot.service"  # systemd user unit
TASK_NAME = "OpenDot"  # Task Scheduler task
DEFAULT_PORT = 8765

System = Literal["darwin", "win32", "linux"]
State = Literal["running", "installed", "not_installed"]


class CompletedLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str]], CompletedLike]


class ServiceError(RuntimeError):
    pass


def normalize_system(platform: str) -> System:
    if platform.startswith("darwin"):
        return "darwin"
    if platform.startswith("win"):
        return "win32"
    if platform.startswith("linux"):
        return "linux"
    raise ServiceError(f"unsupported operating system: {platform}")


def _pure(system: System) -> type[PurePath]:
    return PureWindowsPath if system == "win32" else PurePosixPath


# --- locations (pure) -----------------------------------------------------------------------


def app_data_dir(system: System, env: Mapping[str, str], home: str) -> str:
    """The OS app-data folder OpenDot uses (the desktop app's folder: it holds ``opendot.db``)."""
    path = _pure(system)
    if system == "win32":
        base = env.get("APPDATA") or str(path(home) / "AppData" / "Roaming")
        return str(path(base) / APP_IDENTIFIER)
    if system == "darwin":
        return str(path(home) / "Library" / "Application Support" / APP_IDENTIFIER)
    base = env.get("XDG_DATA_HOME") or str(path(home) / ".local" / "share")
    return str(path(base) / APP_IDENTIFIER)


def log_file_path(system: System, env: Mapping[str, str], home: str) -> str:
    path = _pure(system)
    if system == "darwin":
        return str(path(home) / "Library" / "Logs" / "OpenDot" / "daemon.log")
    return str(path(app_data_dir(system, env, home)) / "logs" / "daemon.log")


def definition_path(system: System, env: Mapping[str, str], home: str) -> str:
    """Where the generated definition is written (the Windows XML is only an install input)."""
    path = _pure(system)
    if system == "darwin":
        return str(path(home) / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist")
    if system == "linux":
        base = env.get("XDG_CONFIG_HOME") or str(path(home) / ".config")
        return str(path(base) / "systemd" / "user" / UNIT_NAME)
    return str(path(app_data_dir(system, env, home)) / "opendot-task.xml")


# --- the command the service runs (pure) ----------------------------------------------------


def service_argv(
    *,
    executable: str,
    frozen: bool,
    db_path: str,
    port: int = DEFAULT_PORT,
    log_file: str | None = None,
) -> list[str]:
    """argv for ``opendot serve``: the frozen sidecar itself, or ``python -m opendot_core.cli``."""
    argv = [executable] if frozen else [executable, "-m", "opendot_core.cli"]
    argv += ["--db", db_path, "serve", "--port", str(port)]
    if log_file:
        argv += ["--log-file", log_file]
    return argv


@dataclass(frozen=True)
class ServiceSpec:
    argv: list[str]
    working_dir: str
    log_file: str
    user: str = ""  # DOMAIN\user on Windows; empty elsewhere


# --- definition generators (pure) -----------------------------------------------------------


def launchd_plist(spec: ServiceSpec) -> str:
    """macOS launchd user agent: start at login, restart only after a non-zero exit."""
    return plistlib.dumps(
        {
            "Label": SERVICE_LABEL,
            "ProgramArguments": list(spec.argv),
            "WorkingDirectory": spec.working_dir,
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 30,
            "ProcessType": "Background",
            "StandardOutPath": spec.log_file,
            "StandardErrorPath": spec.log_file,
        }
    ).decode("utf-8")


def _systemd_quote(arg: str) -> str:
    if arg and not any(c in arg for c in " \t\"'\\$%;"):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


def systemd_unit(spec: ServiceSpec) -> str:
    """Linux systemd user unit: restart on failure, log appended to the app-data folder."""
    exec_start = " ".join(_systemd_quote(arg) for arg in spec.argv)
    return (
        "[Unit]\n"
        "Description=OpenDot daemon (API, UI and always-on loop)\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        f"WorkingDirectory={_systemd_quote(spec.working_dir)}\n"
        "Restart=on-failure\n"
        "RestartSec=10\n"
        f"StandardOutput=append:{spec.log_file}\n"
        f"StandardError=append:{spec.log_file}\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def task_xml(spec: ServiceSpec) -> str:
    """Windows Task Scheduler definition: at logon, current user, no elevation, restart on failure.

    Output goes to the app-data log through ``serve --log-file`` (Task Scheduler cannot redirect).
    """
    command, *rest = spec.argv
    arguments = subprocess.list2cmdline(rest)
    user = f"      <UserId>{escape(spec.user)}</UserId>\n" if spec.user else ""
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        "    <Description>OpenDot daemon (API, UI and always-on loop)</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <LogonTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        f"{user}"
        "    </LogonTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"{user}"
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <RestartOnFailure>\n"
        "      <Interval>PT1M</Interval>\n"
        "      <Count>999</Count>\n"
        "    </RestartOnFailure>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{escape(command)}</Command>\n"
        f"      <Arguments>{escape(arguments)}</Arguments>\n"
        f"      <WorkingDirectory>{escape(spec.working_dir)}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def render_definition(system: System, spec: ServiceSpec) -> str:
    return {"darwin": launchd_plist, "linux": systemd_unit, "win32": task_xml}[system](spec)


# --- install / uninstall / status (talks to the OS through ``run``) --------------------------


@dataclass(frozen=True)
class ServiceStatus:
    state: State
    detail: str = ""

    def render(self) -> str:
        text = {"running": "running", "installed": "installed (not running)", "not_installed": "not installed"}[
            self.state
        ]
        return f"{text}: {self.detail}" if self.detail else text


def default_run(command: list[str]) -> CompletedLike:
    return subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)


class ServiceManager:
    def __init__(
        self,
        system: System,
        *,
        env: Mapping[str, str] | None = None,
        home: str | None = None,
        run: Runner = default_run,
        executable: str | None = None,
        frozen: bool | None = None,
        uid: int | None = None,
        user: str | None = None,
        port: int = DEFAULT_PORT,
    ) -> None:
        self.system = system
        self.env = dict(os.environ if env is None else env)
        self.home = home if home is not None else str(Path.home())
        self.run = run
        self.executable = executable or sys.executable
        self.frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        self.uid = uid if uid is not None else (os.getuid() if hasattr(os, "getuid") else 0)
        self.user = user if user is not None else self._windows_user()
        self.port = port

    def _windows_user(self) -> str:
        if self.system != "win32":
            return ""
        name = self.env.get("USERNAME", "")
        domain = self.env.get("USERDOMAIN", "")
        return f"{domain}\\{name}" if name and domain else name

    @property
    def data_dir(self) -> str:
        return app_data_dir(self.system, self.env, self.home)

    @property
    def log_file(self) -> str:
        return log_file_path(self.system, self.env, self.home)

    @property
    def definition_file(self) -> str:
        return definition_path(self.system, self.env, self.home)

    def spec(self) -> ServiceSpec:
        db_path = str(_pure(self.system)(self.data_dir) / "opendot.db")
        argv = service_argv(
            executable=self.executable,
            frozen=self.frozen,
            db_path=db_path,
            port=self.port,
            log_file=self.log_file if self.system == "win32" else None,
        )
        return ServiceSpec(argv=argv, working_dir=self.data_dir, log_file=self.log_file, user=self.user)

    def definition(self) -> str:
        return render_definition(self.system, self.spec())

    # -- helpers --

    def _exec(self, command: list[str], *, check: bool = True) -> CompletedLike:
        result = self.run(command)
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise ServiceError(f"`{' '.join(command)}` failed (exit {result.returncode}): {detail}")
        return result

    def _fs(self, path: str) -> Path:
        """A path string built for ``self.system`` as a real path on this host."""
        return Path(PureWindowsPath(path).as_posix()) if self.system == "win32" else Path(path)

    def _write_definition(self) -> Path:
        path = self._fs(self.definition_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fs(self.log_file).parent.mkdir(parents=True, exist_ok=True)
        self._fs(self.data_dir).mkdir(parents=True, exist_ok=True)
        text = self.definition()
        if self.system == "win32":
            path.write_bytes(text.encode("utf-16"))  # schtasks /XML expects UTF-16 with a BOM
        else:
            path.write_text(text, encoding="utf-8")
        return path

    def _launchd_domain(self) -> str:
        return f"gui/{self.uid}"

    # -- operations --

    def install(self) -> str:
        path = self._write_definition()
        if self.system == "darwin":
            self._exec(["launchctl", "bootout", f"{self._launchd_domain()}/{SERVICE_LABEL}"], check=False)
            self._exec(["launchctl", "bootstrap", self._launchd_domain(), str(path)])
        elif self.system == "linux":
            self._exec(["systemctl", "--user", "daemon-reload"])
            self._exec(["systemctl", "--user", "enable", "--now", UNIT_NAME])
        else:
            self._exec(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(path), "/F"])
            self._exec(["schtasks", "/Run", "/TN", TASK_NAME], check=False)
        return f"installed ({path}); logs: {self.log_file}"

    def uninstall(self) -> str:
        path = self._fs(self.definition_file)
        if self.system == "darwin":
            self._exec(["launchctl", "bootout", f"{self._launchd_domain()}/{SERVICE_LABEL}"], check=False)
        elif self.system == "linux":
            self._exec(["systemctl", "--user", "disable", "--now", UNIT_NAME], check=False)
        else:
            self._exec(["schtasks", "/End", "/TN", TASK_NAME], check=False)
            self._exec(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False)
        removed = path.exists()
        path.unlink(missing_ok=True)
        if self.system == "linux":
            self._exec(["systemctl", "--user", "daemon-reload"], check=False)
        return "removed" if removed else "nothing to remove"

    def status(self) -> ServiceStatus:
        if self.system == "darwin":
            result = self._exec(["launchctl", "print", f"{self._launchd_domain()}/{SERVICE_LABEL}"], check=False)
            if result.returncode != 0:
                return self._file_status()
            running = "state = running" in result.stdout
            return ServiceStatus("running" if running else "installed")
        if self.system == "linux":
            result = self._exec(["systemctl", "--user", "is-active", UNIT_NAME], check=False)
            if result.stdout.strip() == "active":
                return ServiceStatus("running")
            return self._file_status()
        result = self._exec(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"], check=False)
        if result.returncode != 0:
            return ServiceStatus("not_installed")
        for line in result.stdout.splitlines():
            key, _, value = line.partition(":")
            if key.strip().lower() == "status":
                return ServiceStatus("running" if value.strip().lower() == "running" else "installed")
        return ServiceStatus("installed")

    def _file_status(self) -> ServiceStatus:
        if self._fs(self.definition_file).exists():
            return ServiceStatus("installed")
        return ServiceStatus("not_installed")


# --- CLI ------------------------------------------------------------------------------------


def status(manager: ServiceManager | None = None) -> dict[str, Any]:
    """The shape ``opendot doctor`` reads: {"installed": bool, "running": bool, "detail": str}."""
    import sys

    current = (manager or ServiceManager(normalize_system(sys.platform))).status()
    return {
        "installed": current.state != "not_installed",
        "running": current.state == "running",
        "detail": current.render(),
    }


def register(subparsers: Any) -> None:
    service = subparsers.add_parser("service", help="install OpenDot as a per-user background service (no sudo or admin)")
    service.add_argument("action", choices=["install", "uninstall", "status"])
    service.add_argument("--port", type=int, default=DEFAULT_PORT)
    service.add_argument("--dry-run", action="store_true", help="print the service definition and change nothing")


def run_service(args: Any, *, manager: ServiceManager | None = None, out: Any = None) -> int:
    out = out or sys.stdout
    try:
        manager = manager or ServiceManager(normalize_system(sys.platform), port=args.port)
        if args.dry_run:
            out.write(manager.definition())
            return 0
        if args.action == "install":
            out.write(manager.install() + "\n")
        elif args.action == "uninstall":
            out.write(manager.uninstall() + "\n")
        else:
            out.write(manager.status().render() + "\n")
        return 0
    except (ServiceError, OSError, subprocess.SubprocessError) as error:
        sys.stderr.write(f"opendot service: {error}\n")
        return 1


__all__ = [
    "ServiceError",
    "ServiceManager",
    "ServiceSpec",
    "ServiceStatus",
    "app_data_dir",
    "definition_path",
    "launchd_plist",
    "log_file_path",
    "normalize_system",
    "register",
    "render_definition",
    "run_service",
    "service_argv",
    "systemd_unit",
    "task_xml",
]
