"""Keep-awake: hold a "don't idle-sleep" lock while the user asked for it (M4 task 4.2, section 11).

Off by default. The setting is ``SettingsStore("keep_awake")`` (``api.models.KeepAwakeSettings``).
When ``enabled`` the daemon holds the OS lock; with ``only_while_plugged_in`` it releases it
on battery power. The always-on loop calls :meth:`KeepAwake.tick` periodically.

This adds no model calls (section 8.4): it only asks the OS to stay awake. Closing a laptop
lid usually sleeps the machine anyway, and holding the lock drains the battery; the UI says so.

Adapters, one per OS:

- Windows: ``SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`` through ctypes. The flag
  belongs to the calling thread, so the always-on thread must be the one that acquires and releases.
- macOS: a ``caffeinate -i -w <pid>`` child process (it exits by itself when the daemon does).
- Linux: a ``systemd-inhibit --what=idle`` child that waits on the daemon's pid.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from .api.models import KeepAwakeSettings

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
DEFAULT_EVALUATE_SECONDS = 15.0


class Inhibitor(Protocol):
    def acquire(self) -> bool: ...

    def release(self) -> None: ...

    def is_held(self) -> bool: ...


class PowerSource(Protocol):
    def on_ac_power(self) -> bool | None:
        """True on AC (or a machine with no battery), False on battery, None when unknown."""


# --- decision logic (pure) ------------------------------------------------------------------


def should_hold(settings: KeepAwakeSettings, on_ac: bool | None) -> bool:
    """Whether the lock should be held right now.

    With "only while plugged in", an unknown power state counts as battery: a drained laptop is a
    worse surprise than a missed keep-awake.
    """
    if not settings.enabled:
        return False
    if settings.only_while_plugged_in:
        return on_ac is True
    return True


# --- inhibitor adapters ---------------------------------------------------------------------


class WindowsInhibitor:
    def __init__(self, set_state: Callable[[int], int] | None = None) -> None:
        self._set_state = set_state or self._kernel32
        self._held = False

    @staticmethod
    def _kernel32(flags: int) -> int:
        import ctypes

        function = ctypes.windll.kernel32.SetThreadExecutionState  # type: ignore[attr-defined]
        function.argtypes = [ctypes.c_uint]
        function.restype = ctypes.c_uint
        return int(function(flags))

    def acquire(self) -> bool:
        if self._held:
            return True
        self._held = self._set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) != 0
        return self._held

    def release(self) -> None:
        if self._held:
            self._set_state(ES_CONTINUOUS)
            self._held = False

    def is_held(self) -> bool:
        return self._held


class ChildProcessInhibitor:
    """A lock held by a helper process that lives exactly as long as the lock should."""

    def __init__(self, command: list[str], *, spawn: Callable[..., Any] = subprocess.Popen) -> None:
        self.command = command
        self._spawn = spawn
        self._process: Any = None

    def acquire(self) -> bool:
        if self.is_held():
            return True
        kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "posix":
            kwargs["start_new_session"] = True  # so release can stop the helper's own children too
        try:
            self._process = self._spawn(self.command, **kwargs)
        except OSError:
            self._process = None
            return False
        return True

    def release(self) -> None:
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:  # pragma: no cover - POSIX helpers only
                process.terminate()
        except (OSError, ProcessLookupError):
            process.terminate()

    def is_held(self) -> bool:
        return self._process is not None and self._process.poll() is None


def macos_inhibitor(pid: int | None = None, spawn: Callable[..., Any] = subprocess.Popen) -> ChildProcessInhibitor:
    return ChildProcessInhibitor(["caffeinate", "-i", "-w", str(pid or os.getpid())], spawn=spawn)


def linux_inhibitor(pid: int | None = None, spawn: Callable[..., Any] = subprocess.Popen) -> ChildProcessInhibitor:
    return ChildProcessInhibitor(
        [
            "systemd-inhibit",
            "--what=idle",
            "--who=OpenDot",
            "--why=OpenDot is keeping the daemon awake",
            "--mode=block",
            "tail",
            f"--pid={pid or os.getpid()}",
            "-f",
            "/dev/null",
        ],
        spawn=spawn,
    )


def default_inhibitor() -> Inhibitor:
    if sys.platform.startswith("win"):
        return WindowsInhibitor()
    if sys.platform == "darwin":
        return macos_inhibitor()
    return linux_inhibitor()


# --- power sources --------------------------------------------------------------------------




def windows_ac_status(read_line_status: Callable[[], int] | None = None) -> bool | None:
    """GetSystemPowerStatus ACLineStatus: 0 battery, 1 AC, 255 unknown."""
    try:
        status = (read_line_status or _read_windows_ac_line_status)()
    except Exception:
        return None
    return {0: False, 1: True}.get(status)


def _read_windows_ac_line_status() -> int:  # pragma: no cover - needs Windows
    import ctypes
    from ctypes import wintypes

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", wintypes.BYTE),
            ("BatteryFlag", wintypes.BYTE),
            ("BatteryLifePercent", wintypes.BYTE),
            ("SystemStatusFlag", wintypes.BYTE),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    status = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):  # type: ignore[attr-defined]
        raise OSError("GetSystemPowerStatus failed")
    return int(status.ACLineStatus)


def parse_pmset_batt(output: str) -> bool | None:
    """`pmset -g batt` starts with "Now drawing from 'AC Power'" or "'Battery Power'"."""
    first = output.strip().splitlines()[0] if output.strip() else ""
    if "AC Power" in first:
        return True
    if "Battery Power" in first:
        return False
    return None


def macos_ac_status(run: Callable[[list[str]], Any] | None = None) -> bool | None:
    try:
        result = (run or _run_text)(["pmset", "-g", "batt"])
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return parse_pmset_batt(result.stdout)


def _run_text(command: list[str]) -> Any:
    return subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)


def linux_ac_status(root: Path | str = "/sys/class/power_supply") -> bool | None:
    """AC when a mains supply is online; battery when mains exist but are all offline.

    With no mains entry, a discharging battery means battery; any other battery state or no power
    supply at all (a desktop) counts as plugged in.
    """
    base = Path(root)
    try:
        supplies = sorted(path for path in base.iterdir())
    except OSError:
        return None

    def read(supply: Path, name: str) -> str:
        try:
            return (supply / name).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    mains = [read(s, "online") for s in supplies if read(s, "type") == "Mains"]
    if "1" in mains:
        return True
    if mains:
        return False
    batteries = [read(s, "status") for s in supplies if read(s, "type") == "Battery"]
    if any(state == "Discharging" for state in batteries):
        return False
    return True


class DefaultPowerSource:
    def on_ac_power(self) -> bool | None:
        if sys.platform.startswith("win"):
            return windows_ac_status()
        if sys.platform == "darwin":
            return macos_ac_status()
        return linux_ac_status()


# --- controller -----------------------------------------------------------------------------


class KeepAwake:
    """Reads the setting, asks the power source, and holds or releases the lock."""

    def __init__(
        self,
        read_settings: Callable[[], KeepAwakeSettings],
        inhibitor: Inhibitor,
        power: PowerSource,
        *,
        clock: Callable[[], float] = time.monotonic,
        evaluate_seconds: float = DEFAULT_EVALUATE_SECONDS,
    ) -> None:
        self._read_settings = read_settings
        self.inhibitor = inhibitor
        self.power = power
        self._clock = clock
        self._evaluate_seconds = evaluate_seconds
        self._last_evaluated: float | None = None

    @classmethod
    def from_store(cls, store: Any, inhibitor: Inhibitor | None = None, power: PowerSource | None = None, **kwargs: Any) -> KeepAwake:
        default = KeepAwakeSettings(enabled=False)
        return cls(
            lambda: store.get("keep_awake", KeepAwakeSettings, default),
            inhibitor or default_inhibitor(),
            power or DefaultPowerSource(),
            **kwargs,
        )

    def evaluate(self) -> bool:
        """Apply the setting now; returns whether the lock is held afterwards."""
        settings = self._read_settings()
        on_ac = self.power.on_ac_power() if settings.enabled and settings.only_while_plugged_in else None
        if should_hold(settings, on_ac):
            self.inhibitor.acquire()  # re-acquires a helper that died
        else:
            self.inhibitor.release()
        return self.inhibitor.is_held()

    def tick(self) -> bool | None:
        """Called every loop cycle; evaluates at most once per ``evaluate_seconds``."""
        now = self._clock()
        if self._last_evaluated is not None and now - self._last_evaluated < self._evaluate_seconds:
            return None
        self._last_evaluated = now
        return self.evaluate()

    def close(self) -> None:
        self.inhibitor.release()


__all__ = [
    "ChildProcessInhibitor",
    "DefaultPowerSource",
    "KeepAwake",
    "WindowsInhibitor",
    "default_inhibitor",
    "linux_ac_status",
    "linux_inhibitor",
    "macos_ac_status",
    "macos_inhibitor",
    "parse_pmset_batt",
    "should_hold",
    "windows_ac_status",
]
