from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from opendot_core import keep_awake
from opendot_core.api.models import KeepAwakeSettings
from opendot_core.db import Database
from opendot_core.keep_awake import (
    ES_CONTINUOUS,
    ES_SYSTEM_REQUIRED,
    ChildProcessInhibitor,
    KeepAwake,
    WindowsInhibitor,
    linux_ac_status,
    linux_inhibitor,
    macos_ac_status,
    macos_inhibitor,
    parse_pmset_batt,
    should_hold,
    windows_ac_status,
)
from opendot_core.settings_store import SettingsStore


def settings(enabled: bool, plugged: bool = True) -> KeepAwakeSettings:
    return KeepAwakeSettings(enabled=enabled, only_while_plugged_in=plugged)


@pytest.mark.parametrize(
    ("enabled", "plugged_only", "on_ac", "expected"),
    [
        (False, True, True, False),
        (False, False, True, False),
        (True, True, True, True),
        (True, True, False, False),
        (True, True, None, False),  # unknown power counts as battery
        (True, False, False, True),
        (True, False, None, True),
    ],
)
def test_should_hold_decision_table(enabled: bool, plugged_only: bool, on_ac: bool | None, expected: bool) -> None:
    assert should_hold(settings(enabled, plugged_only), on_ac) is expected


def test_off_by_default_when_nothing_is_stored(tmp_path: Path) -> None:
    inhibitor = FakeInhibitor()
    awake = KeepAwake.from_store(SettingsStore(Database(tmp_path / "k.db")), inhibitor, FakePower(True))
    assert awake.evaluate() is False
    assert inhibitor.acquired == 0


class FakeInhibitor:
    def __init__(self) -> None:
        self.held = False
        self.acquired = 0
        self.released = 0

    def acquire(self) -> bool:
        self.acquired += 1
        self.held = True
        return True

    def release(self) -> None:
        if self.held:
            self.released += 1
        self.held = False

    def is_held(self) -> bool:
        return self.held


class FakePower:
    def __init__(self, ac: bool | None) -> None:
        self.ac = ac
        self.calls = 0

    def on_ac_power(self) -> bool | None:
        self.calls += 1
        return self.ac


def test_controller_follows_the_setting_and_the_power_source(tmp_path: Path) -> None:
    store = SettingsStore(Database(tmp_path / "k.db"))
    inhibitor, power = FakeInhibitor(), FakePower(True)
    awake = KeepAwake.from_store(store, inhibitor, power)
    store.set("keep_awake", settings(True))
    assert awake.evaluate() is True
    power.ac = False  # unplugged: released
    assert awake.evaluate() is False
    power.ac = True  # plugged back in: held again
    assert awake.evaluate() is True
    store.set("keep_awake", settings(False))
    assert awake.evaluate() is False
    assert inhibitor.released == 2


def test_power_is_not_polled_unless_it_matters(tmp_path: Path) -> None:
    store = SettingsStore(Database(tmp_path / "k.db"))
    power = FakePower(True)
    awake = KeepAwake.from_store(store, FakeInhibitor(), power)
    store.set("keep_awake", settings(True, plugged=False))
    awake.evaluate()
    store.set("keep_awake", settings(False))
    awake.evaluate()
    assert power.calls == 0


def test_tick_evaluates_at_most_once_per_interval() -> None:
    now = [0.0]
    reads: list[int] = []

    def read() -> KeepAwakeSettings:
        reads.append(1)
        return settings(True, plugged=False)

    awake = KeepAwake(read, FakeInhibitor(), FakePower(True), clock=lambda: now[0], evaluate_seconds=15)
    assert awake.tick() is True
    now[0] = 5
    assert awake.tick() is None
    now[0] = 16
    assert awake.tick() is True
    assert len(reads) == 2


def test_close_releases_the_lock() -> None:
    inhibitor = FakeInhibitor()
    awake = KeepAwake(lambda: settings(True, plugged=False), inhibitor, FakePower(True))
    awake.evaluate()
    awake.close()
    assert inhibitor.held is False


def test_a_helper_that_died_is_started_again() -> None:
    class Process:
        def __init__(self) -> None:
            self.alive = True

        def poll(self) -> int | None:
            return None if self.alive else 1

    spawned: list[Process] = []

    def spawn(command: list[str], **kwargs: Any) -> Process:
        spawned.append(Process())
        return spawned[-1]

    inhibitor = ChildProcessInhibitor(["x"], spawn=spawn)
    awake = KeepAwake(lambda: settings(True, plugged=False), inhibitor, FakePower(True))
    awake.evaluate()
    spawned[0].alive = False
    assert awake.evaluate() is True
    assert len(spawned) == 2


# --- adapters -------------------------------------------------------------------------------


def test_windows_inhibitor_sets_and_clears_the_execution_state() -> None:
    calls: list[int] = []
    inhibitor = WindowsInhibitor(lambda flags: calls.append(flags) or 1)
    assert inhibitor.acquire() and inhibitor.is_held()
    assert inhibitor.acquire()  # idempotent
    inhibitor.release()
    assert calls == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED, ES_CONTINUOUS]
    assert not inhibitor.is_held()
    inhibitor.release()
    assert len(calls) == 2


def test_windows_inhibitor_reports_failure() -> None:
    assert WindowsInhibitor(lambda flags: 0).acquire() is False


def test_windows_ac_status() -> None:
    assert windows_ac_status(lambda: 1) is True
    assert windows_ac_status(lambda: 0) is False
    assert windows_ac_status(lambda: 255) is None

    def boom() -> int:
        raise OSError("no")

    assert windows_ac_status(boom) is None


def test_macos_inhibitor_runs_caffeinate_against_our_pid() -> None:
    commands: list[list[str]] = []

    class Process:
        def poll(self) -> None:
            return None

    inhibitor = macos_inhibitor(4242, spawn=lambda command, **kwargs: commands.append(command) or Process())
    assert inhibitor.acquire() and inhibitor.is_held()
    assert commands == [["caffeinate", "-i", "-w", "4242"]]


def test_linux_inhibitor_runs_systemd_inhibit_for_idle_and_follows_our_pid() -> None:
    commands: list[list[str]] = []

    class Process:
        def poll(self) -> None:
            return None

    inhibitor = linux_inhibitor(77, spawn=lambda command, **kwargs: commands.append(command) or Process())
    inhibitor.acquire()
    assert commands[0][:2] == ["systemd-inhibit", "--what=idle"]
    assert "--pid=77" in commands[0]


def test_child_inhibitor_missing_binary_does_not_crash() -> None:
    def spawn(command: list[str], **kwargs: Any) -> None:
        raise FileNotFoundError("caffeinate")

    inhibitor = ChildProcessInhibitor(["caffeinate"], spawn=spawn)
    assert inhibitor.acquire() is False and not inhibitor.is_held()


def test_child_inhibitor_release_terminates_the_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(keep_awake.os, "killpg", lambda pid, sig: killed.append((pid, sig)), raising=False)
    monkeypatch.setattr(keep_awake.os, "name", "posix")

    class Process:
        pid = 99
        terminated = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True

    process = Process()
    inhibitor = ChildProcessInhibitor(["x"], spawn=lambda command, **kwargs: process)
    inhibitor.acquire()
    inhibitor.release()
    assert killed == [(99, keep_awake.signal.SIGTERM)]
    assert not inhibitor.is_held()


def test_parse_pmset_batt() -> None:
    ac = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged; 0:00 remaining\n"
    battery = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t61%; discharging; 3:10 remaining\n"
    assert parse_pmset_batt(ac) is True
    assert parse_pmset_batt(battery) is False
    assert parse_pmset_batt("") is None
    assert parse_pmset_batt("something else") is None


def test_macos_ac_status_runs_pmset() -> None:
    class Result:
        returncode = 0
        stdout = "Now drawing from 'Battery Power'\n"

    seen: list[list[str]] = []
    assert macos_ac_status(lambda command: seen.append(command) or Result()) is False
    assert seen == [["pmset", "-g", "batt"]]

    def boom(command: list[str]) -> Any:
        raise FileNotFoundError

    assert macos_ac_status(boom) is None


def _supply(root: Path, name: str, **files: str) -> None:
    folder = root / name
    folder.mkdir()
    for key, value in files.items():
        (folder / key).write_text(value + "\n", encoding="utf-8")


def test_linux_ac_status_from_sysfs(tmp_path: Path) -> None:
    _supply(tmp_path, "AC", type="Mains", online="1")
    _supply(tmp_path, "BAT0", type="Battery", status="Charging")
    assert linux_ac_status(tmp_path) is True
    (tmp_path / "AC" / "online").write_text("0\n", encoding="utf-8")
    assert linux_ac_status(tmp_path) is False


def test_linux_ac_status_without_a_mains_entry(tmp_path: Path) -> None:
    _supply(tmp_path, "BAT0", type="Battery", status="Discharging")
    assert linux_ac_status(tmp_path) is False
    (tmp_path / "BAT0" / "status").write_text("Full\n", encoding="utf-8")
    assert linux_ac_status(tmp_path) is True


def test_linux_ac_status_for_a_desktop_and_a_missing_folder(tmp_path: Path) -> None:
    assert linux_ac_status(tmp_path) is True  # no power supplies at all: a desktop
    assert linux_ac_status(tmp_path / "nope") is None


def test_keep_awake_makes_no_model_calls() -> None:
    source = Path(keep_awake.__file__).read_text(encoding="utf-8")
    assert "providers" not in source and "router" not in source
