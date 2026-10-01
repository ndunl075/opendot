from __future__ import annotations

import plistlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import pytest

from opendot_core import service
from opendot_core.cli import build_parser, main
from opendot_core.service import (
    ServiceError,
    ServiceManager,
    ServiceSpec,
    app_data_dir,
    launchd_plist,
    log_file_path,
    normalize_system,
    service_argv,
    systemd_unit,
    task_xml,
)


def test_service_argv_uses_python_module_or_the_frozen_sidecar() -> None:
    plain = service_argv(executable="/venv/bin/python", frozen=False, db_path="/d/opendot.db", port=8765)
    assert plain == ["/venv/bin/python", "-m", "opendot_core.cli", "--db", "/d/opendot.db", "serve", "--port", "8765"]
    frozen = service_argv(executable="C:/App/opendot-daemon.exe", frozen=True, db_path="d.db", log_file="l.log")
    assert frozen == ["C:/App/opendot-daemon.exe", "--db", "d.db", "serve", "--port", "8765", "--log-file", "l.log"]


def test_app_data_and_log_locations_per_os() -> None:
    assert app_data_dir("win32", {"APPDATA": "C:\\Users\\a\\AppData\\Roaming"}, "C:\\Users\\a") == (
        "C:\\Users\\a\\AppData\\Roaming\\org.opendot.desktop"
    )
    assert app_data_dir("darwin", {}, "/Users/a") == "/Users/a/Library/Application Support/org.opendot.desktop"
    assert app_data_dir("linux", {}, "/home/a") == "/home/a/.local/share/org.opendot.desktop"
    assert app_data_dir("linux", {"XDG_DATA_HOME": "/x"}, "/home/a") == "/x/org.opendot.desktop"
    assert log_file_path("darwin", {}, "/Users/a") == "/Users/a/Library/Logs/OpenDot/daemon.log"
    assert log_file_path("linux", {}, "/home/a").endswith("org.opendot.desktop/logs/daemon.log")
    assert log_file_path("win32", {"APPDATA": "C:\\R"}, "C:\\Users\\a") == "C:\\R\\org.opendot.desktop\\logs\\daemon.log"


def test_launchd_plist_restarts_on_failure_and_logs_to_library_logs() -> None:
    spec = ServiceSpec(argv=["/py", "-m", "opendot_core.cli", "serve"], working_dir="/data", log_file="/logs/d.log")
    data = plistlib.loads(launchd_plist(spec).encode())
    assert data["Label"] == "org.opendot.daemon"
    assert data["ProgramArguments"] == ["/py", "-m", "opendot_core.cli", "serve"]
    assert data["RunAtLoad"] is True
    assert data["KeepAlive"] == {"SuccessfulExit": False}
    assert data["StandardOutPath"] == data["StandardErrorPath"] == "/logs/d.log"
    assert data["WorkingDirectory"] == "/data"


def test_systemd_unit_quotes_paths_with_spaces_and_restarts_on_failure() -> None:
    spec = ServiceSpec(
        argv=["/home/a b/py", "-m", "opendot_core.cli", "--db", "/home/a b/opendot.db", "serve"],
        working_dir="/home/a b",
        log_file="/home/a/logs/daemon.log",
    )
    unit = systemd_unit(spec)
    assert '"/home/a b/py" -m opendot_core.cli --db "/home/a b/opendot.db" serve' in unit
    assert "Restart=on-failure" in unit
    assert "StandardOutput=append:/home/a/logs/daemon.log" in unit
    assert "WantedBy=default.target" in unit
    assert "User=" not in unit  # a user unit never names a user (and never needs root)


def test_task_xml_is_at_logon_for_the_current_user_without_elevation() -> None:
    spec = ServiceSpec(
        argv=["C:\\Program Files\\OpenDot\\opendot-daemon.exe", "--db", "C:\\Users\\a b\\opendot.db", "serve", "--log-file", "C:\\l.log"],
        working_dir="C:\\Users\\a b",
        log_file="C:\\l.log",
        user="PC\\a",
    )
    xml = task_xml(spec)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    root = ET.fromstring(xml.split("?>", 1)[1])
    assert root.find("t:Triggers/t:LogonTrigger", ns) is not None
    assert root.findtext("t:Principals/t:Principal/t:RunLevel", namespaces=ns) == "LeastPrivilege"
    assert root.findtext("t:Principals/t:Principal/t:UserId", namespaces=ns) == "PC\\a"
    assert root.findtext("t:Settings/t:RestartOnFailure/t:Interval", namespaces=ns) == "PT1M"
    assert root.findtext("t:Settings/t:StopIfGoingOnBatteries", namespaces=ns) == "false"
    assert root.findtext("t:Actions/t:Exec/t:Command", namespaces=ns) == "C:\\Program Files\\OpenDot\\opendot-daemon.exe"
    arguments = root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns)
    assert '"C:\\Users\\a b\\opendot.db"' in arguments and arguments.endswith("--log-file C:\\l.log")


def test_task_xml_escapes_special_characters() -> None:
    xml = task_xml(ServiceSpec(argv=["C:\\a&b\\x.exe", "serve"], working_dir="C:\\w", log_file="l", user="D\\u<"))
    ET.fromstring(xml.split("?>", 1)[1])  # still well-formed


@dataclass
class Result:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class FakeRun:
    def __init__(self, responses: dict[tuple[str, ...], Result] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.responses = responses or {}

    def __call__(self, command: list[str]) -> Result:
        self.calls.append(command)
        for prefix, result in self.responses.items():
            if tuple(command[: len(prefix)]) == prefix:
                return result
        return Result()


def manager(system: str, tmp_path: Path, run: FakeRun) -> ServiceManager:
    home = tmp_path.as_posix()
    env = {"APPDATA": f"{home}/AppData/Roaming", "USERNAME": "a", "USERDOMAIN": "PC"}
    return ServiceManager(system, env=env, home=home, run=run, executable="/py", frozen=False, uid=501)  # type: ignore[arg-type]


def test_install_macos_writes_plist_and_bootstraps(tmp_path: Path) -> None:
    run = FakeRun()
    message = manager("darwin", tmp_path, run).install()
    plist = tmp_path / "Library" / "LaunchAgents" / "org.opendot.daemon.plist"
    assert plist.exists() and "org.opendot.daemon" in message
    assert ["launchctl", "bootstrap", "gui/501", str(plist)] in run.calls
    assert all("sudo" not in part for call in run.calls for part in call)


def test_install_linux_writes_unit_and_enables_it(tmp_path: Path) -> None:
    run = FakeRun()
    manager("linux", tmp_path, run).install()
    unit = tmp_path / ".config" / "systemd" / "user" / "opendot.service"
    assert "Restart=on-failure" in unit.read_text(encoding="utf-8")
    assert ["systemctl", "--user", "daemon-reload"] in run.calls
    assert ["systemctl", "--user", "enable", "--now", "opendot.service"] in run.calls


def test_install_windows_creates_task_from_utf16_xml_and_runs_it(tmp_path: Path) -> None:
    run = FakeRun()
    manager("win32", tmp_path, run).install()
    create = next(call for call in run.calls if call[:2] == ["schtasks", "/Create"])
    xml_path = Path(create[create.index("/XML") + 1])
    assert xml_path.read_bytes()[:2] in (b"\xff\xfe", b"\xfe\xff")
    assert "LogonTrigger" in xml_path.read_text(encoding="utf-16")
    assert create[-1] == "/F"
    assert ["schtasks", "/Run", "/TN", "OpenDot"] in run.calls


def test_install_failure_raises_with_the_command_output(tmp_path: Path) -> None:
    run = FakeRun({("systemctl", "--user", "enable"): Result(1, "", "Failed to connect to bus")})
    with pytest.raises(ServiceError, match="Failed to connect to bus"):
        manager("linux", tmp_path, run).install()


@pytest.mark.parametrize("system", ["darwin", "linux", "win32"])
def test_uninstall_removes_definition_and_is_idempotent(system: str, tmp_path: Path) -> None:
    run = FakeRun()
    target = manager(system, tmp_path, run)
    target.install()
    assert Path(target.definition_file.replace("\\", "/")).exists()
    assert target.uninstall() == "removed"
    assert not Path(target.definition_file.replace("\\", "/")).exists()
    assert target.uninstall() == "nothing to remove"


def test_status_macos(tmp_path: Path) -> None:
    running = FakeRun({("launchctl", "print"): Result(0, "state = running\n")})
    assert manager("darwin", tmp_path, running).status().state == "running"
    loaded = FakeRun({("launchctl", "print"): Result(0, "state = waiting\n")})
    assert manager("darwin", tmp_path, loaded).status().state == "installed"
    missing = FakeRun({("launchctl", "print"): Result(113, "", "Could not find service")})
    assert manager("darwin", tmp_path, missing).status().state == "not_installed"


def test_status_linux(tmp_path: Path) -> None:
    run = FakeRun({("systemctl", "--user", "is-active"): Result(0, "active\n")})
    assert manager("linux", tmp_path, run).status().state == "running"
    inactive = FakeRun({("systemctl", "--user", "is-active"): Result(3, "inactive\n")})
    assert manager("linux", tmp_path, inactive).status().state == "not_installed"
    inactive_manager = manager("linux", tmp_path, inactive)
    inactive_manager.install()
    assert inactive_manager.status().state == "installed"


def test_status_windows(tmp_path: Path) -> None:
    listing = "Folder: \\\nHostName: PC\nTaskName: \\OpenDot\nStatus: Running\n"
    assert manager("win32", tmp_path, FakeRun({("schtasks", "/Query"): Result(0, listing)})).status().state == "running"
    ready = listing.replace("Running", "Ready")
    assert manager("win32", tmp_path, FakeRun({("schtasks", "/Query"): Result(0, ready)})).status().state == "installed"
    gone = FakeRun({("schtasks", "/Query"): Result(1, "", "ERROR: The system cannot find the file specified.")})
    assert manager("win32", tmp_path, gone).status().state == "not_installed"


def test_status_render_words() -> None:
    assert service.ServiceStatus("running").render() == "running"
    assert service.ServiceStatus("installed").render() == "installed (not running)"
    assert service.ServiceStatus("not_installed").render() == "not installed"


def test_normalize_system() -> None:
    assert normalize_system("win32") == "win32"
    assert normalize_system("darwin") == "darwin"
    assert normalize_system("linux") == "linux"
    with pytest.raises(ServiceError):
        normalize_system("freebsd13")


def test_cli_dry_run_prints_the_definition_without_touching_the_os(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["service", "install", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "opendot_core.cli" in out and "serve" in out


def test_cli_service_commands_use_the_manager(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = FakeRun({("systemctl", "--user", "is-active"): Result(0, "active\n")})
    args = build_parser().parse_args(["service", "status"])
    assert service.run_service(args, manager=manager("linux", tmp_path, run)) == 0
    assert capsys.readouterr().out.strip() == "running"
    failing = FakeRun({("systemctl", "--user", "daemon-reload"): Result(1, "", "no bus")})
    args = build_parser().parse_args(["service", "install"])
    assert service.run_service(args, manager=manager("linux", tmp_path, failing)) == 1
    assert "no bus" in capsys.readouterr().err
