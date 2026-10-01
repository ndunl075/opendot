"""The always-on work inside `opendot serve` (M4 task 4.1 to 4.3).

One service process gives the API, the UI and this background thread, so a single launchd,
Task Scheduler or systemd entry is enough. Each cycle:

1. keep-awake: apply the setting (hold or release the OS lock; see ``keep_awake``);
2. catch-up: on start or after a wall-clock jump, skip stale recurring runs and remember what was
   missed (see ``catch_up``);
3. ``OpenDotRunner.run_once``: due jobs and reminders into the outbox, outbox delivery, connector
   syncs. Telegram stays off unless a pairing is configured (it is off by default until v0.2);
4. the one-time "while your computer was asleep" note.

Everything here is code-first: no model calls (section 8.4).
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime

from .audit import AuditEvent, AuditLog
from .catch_up import CatchUp
from .db import Database
from .keep_awake import KeepAwake
from .runner import ConnectorSync, OpenDotRunner
from .secret_store import SecretStoreError
from .settings_store import SettingsStore

RunnerFactory = Callable[[Database], AbstractContextManager[OpenDotRunner]]


def quiet_when_unconfigured(sync: Callable[[], None]) -> Callable[[], None]:
    """A connector that has no credentials yet is skipped, not logged as an error every cycle."""

    def run() -> None:
        try:
            sync()
        except SecretStoreError:
            return

    return run


@contextmanager
def default_runner(database: Database) -> Iterator[OpenDotRunner]:
    """The `opendot run` runner with its default arguments (no Telegram or Slack pairing)."""
    from .cli import build_parser, running_opendot_runner

    args = build_parser().parse_args(["run"])
    with running_opendot_runner(database, args) as runner:
        runner.connectors = tuple(
            ConnectorSync(c.name, c.interval_seconds, quiet_when_unconfigured(c.run)) for c in runner.connectors
        )
        runner.background_connectors = True  # a slow sync must never delay due reminders
        yield runner


class AlwaysOnWorker:
    def __init__(
        self,
        database: Database,
        *,
        runner_factory: RunnerFactory = default_runner,
        keep_awake: KeepAwake | None = None,
        catch_up: CatchUp | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.database = database
        self.runner_factory = runner_factory
        self.keep_awake = keep_awake or KeepAwake.from_store(SettingsStore(database))
        self.catch_up = catch_up or CatchUp(database, clock=clock)
        self.clock = clock
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def cycle(self, runner: OpenDotRunner) -> bool:
        """One pass of the loop; returns True when the runner asked the daemon to restart."""
        self._guard("keep_awake", self.keep_awake.tick)
        report = None
        detected = self.catch_up.detect()
        if detected is not None:
            cause, gap = detected
            report = self._guard(
                "catch_up",
                lambda: self.catch_up.prepare(cause, gap, [c.interval_seconds for c in runner.connectors]),
            )
            if report is not None:
                runner.mark_connectors_due()
        self._guard("runner", runner.run_once)
        if report is not None:
            self._guard("catch_up_note", lambda: self.catch_up.announce(report))
        self.catch_up.mark_cycle_done()
        return bool(self._guard("restart", runner.handle_restart_request))

    def _guard(self, context: str, action: Callable[[], object]) -> object:
        try:
            return action()
        except Exception as error:  # the loop must outlive any single failure
            reason = f"{error.__class__.__name__}: {error}"
            print(f"[opendot serve] {context} failed: {reason}", file=sys.stderr)
            try:
                AuditLog(self.database).append(
                    AuditEvent(actor="system:runner", client="always_on", tool=context, outcome="error", result={"error": reason})
                )
            except Exception:
                pass
            return None

    def _main(self) -> None:
        try:
            with self.runner_factory(self.database) as runner:
                while not self.stop_event.is_set():
                    if self.cycle(runner):
                        break
                    self.stop_event.wait(runner.idle_sleep_seconds)
        except Exception as error:
            print(f"[opendot serve] background loop stopped: {error.__class__.__name__}: {error}", file=sys.stderr)
        finally:
            self.keep_awake.close()  # the Windows lock belongs to this thread

    def start(self) -> None:
        self._thread = threading.Thread(target=self._main, name="opendot-always-on", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)


__all__ = ["AlwaysOnWorker", "default_runner", "quiet_when_unconfigured"]
