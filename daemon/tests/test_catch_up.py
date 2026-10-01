from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from opendot_core.always_on import AlwaysOnWorker
from opendot_core.brief_schedule import create_daily
from opendot_core.catch_up import UI_DESTINATION, CatchUp, CatchUpReport
from opendot_core.db import Database
from opendot_core.keep_awake import KeepAwake
from opendot_core.reminders import ReminderStore
from opendot_core.runner import ConnectorSync, OpenDotRunner
from opendot_core.runtime_control import record_heartbeat

T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def remind(database: Database, run_at: datetime, text: str, *, daily: bool = False, key: str | None = None) -> str:
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            return ReminderStore.create(
                connection,
                run_at=run_at,
                task_id="t-" + text,
                destination="telegram:20",
                text=text,
                idempotency_key=key or f"r-{text}",
                daily=daily,
                timezone_name="UTC" if daily else None,
            ).id


def outbox_rows(database: Database) -> list[dict]:
    with database.connect() as connection:
        rows = connection.execute("SELECT destination, payload_json, job_id FROM outbox ORDER BY created_at, id").fetchall()
    return [{"destination": r["destination"], "payload": json.loads(r["payload_json"]), "job_id": r["job_id"]} for r in rows]


def job_row(database: Database, job_id: str):
    with database.connect() as connection:
        return connection.execute("SELECT state, next_run_at FROM jobs WHERE id = ?", (job_id,)).fetchone()


def make_worker(database: Database, clock: Clock, runner: OpenDotRunner) -> AlwaysOnWorker:
    class NeverAwake:
        def tick(self):  # noqa: ANN202
            return None

        def close(self) -> None:
            return None

    worker = AlwaysOnWorker(
        database,
        keep_awake=NeverAwake(),  # type: ignore[arg-type]
        catch_up=CatchUp(database, clock=clock),
        clock=clock,
    )
    return worker


def make_runner(database: Database, clock: Clock, connectors: tuple[ConnectorSync, ...] = ()) -> OpenDotRunner:
    return OpenDotRunner(database, wall_clock=clock, connectors=connectors, idle_sleep_seconds=0)


def test_a_short_gap_is_not_a_wake(tmp_path: Path) -> None:
    database = Database(tmp_path / "c.db")
    clock = Clock()
    catch_up = CatchUp(database, clock=clock)
    assert catch_up.detect() is None  # fresh install: nothing to compare with
    catch_up.mark_cycle_done()
    clock.advance(seconds=30)
    assert catch_up.detect() is None


def test_a_wall_clock_jump_bigger_than_the_interval_is_a_wake(tmp_path: Path) -> None:
    database = Database(tmp_path / "c.db")
    clock = Clock()
    catch_up = CatchUp(database, clock=clock)
    catch_up.detect()
    catch_up.mark_cycle_done()
    clock.advance(hours=8)
    assert catch_up.detect() == ("wake", 8 * 3600)


def test_start_compares_with_the_last_heartbeat(tmp_path: Path) -> None:
    database = Database(tmp_path / "c.db")
    record_heartbeat(database, now=T0 - timedelta(hours=3))
    catch_up = CatchUp(database, clock=Clock())
    assert catch_up.detect() == ("start", 3 * 3600)
    assert catch_up.detect() is None or catch_up.detect()[0] == "wake"  # type: ignore[index]


def test_start_right_after_a_heartbeat_is_not_a_catch_up(tmp_path: Path) -> None:
    database = Database(tmp_path / "c.db")
    record_heartbeat(database, now=T0 - timedelta(seconds=20))
    assert CatchUp(database, clock=Clock()).detect() is None


def test_missed_one_shot_reminders_run_late_and_one_note_is_enqueued(tmp_path: Path) -> None:
    database = Database(tmp_path / "c.db")
    clock = Clock()
    runner = make_runner(database, clock)
    worker = make_worker(database, clock, runner)
    worker.cycle(runner)  # establishes the reference time
    remind(database, T0 + timedelta(hours=1), "stand up")
    remind(database, T0 + timedelta(hours=2), "call mum")
    clock.advance(hours=6)  # the computer slept through both
    worker.cycle(runner)

    rows = outbox_rows(database)
    texts = [row["payload"]["text"] for row in rows if row["job_id"]]
    assert len(texts) == 2 and all(t.startswith("Late reminder (scheduled ") for t in texts)
    notes = [row for row in rows if row["job_id"] is None]
    assert len(notes) == 1
    assert notes[0]["destination"] == "telegram:20"
    assert notes[0]["payload"]["text"] == "While your computer was asleep: 2 reminders (delivered late)."

    clock.advance(seconds=5)
    worker.cycle(runner)  # no second note
    assert len([r for r in outbox_rows(database) if r["job_id"] is None]) == 1


def test_a_daily_reminder_missed_for_days_runs_once_and_stale_runs_are_skipped(tmp_path: Path) -> None:
    database = Database(tmp_path / "c.db")
    clock = Clock()
    job_id = remind(database, T0 + timedelta(hours=1), "stretch", daily=True)  # daily 09:00
    runner = make_runner(database, clock)
    worker = make_worker(database, clock, runner)
    worker.cycle(runner)
    clock.advance(days=3, hours=2, minutes=30)  # now 2026-10-04 10:30; 09:00 on the 1st, 2nd, 3rd and 4th were missed
    worker.cycle(runner)

    delivered = [row["payload"]["text"] for row in outbox_rows(database) if row["job_id"] == job_id]
    assert len(delivered) == 1
    assert "2026-10-04T09:00:00" in delivered[0]  # the latest missed run, not the oldest
    note = [row["payload"]["text"] for row in outbox_rows(database) if row["job_id"] is None]
    assert note == [
        "While your computer was asleep: 1 reminder (delivered late), 3 older runs of recurring checks skipped."
    ]
    assert job_row(database, job_id)["next_run_at"].startswith("2026-10-05T09:00:00")


def test_a_missed_daily_brief_runs_once(tmp_path: Path) -> None:
    database = Database(tmp_path / "c.db")
    clock = Clock()
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            create_daily(
                connection, destination="telegram:20", local_time=time(7, 0), timezone_name="UTC", now=T0
            )
    runner = make_runner(database, clock)
    worker = make_worker(database, clock, runner)
    worker.cycle(runner)
    clock.advance(days=2)
    worker.cycle(runner)
    briefs = [row for row in outbox_rows(database) if row["job_id"] is not None]
    assert len(briefs) == 1
    note = next(r["payload"]["text"] for r in outbox_rows(database) if r["job_id"] is None)
    assert "1 morning brief (delivered late)" in note and "older run" in note


def test_syncs_run_once_on_wake_and_are_counted_not_replayed(tmp_path: Path) -> None:
    database = Database(tmp_path / "c.db")
    clock = Clock()
    ran: list[str] = []
    connectors = (
        ConnectorSync("gmail", 300, lambda: ran.append("gmail")),
        ConnectorSync("calendar_history", 604800, lambda: ran.append("history")),
    )
    runner = make_runner(database, clock, connectors)
    worker = make_worker(database, clock, runner)
    worker.cycle(runner)
    ran.clear()
    clock.advance(hours=5)
    worker.cycle(runner)
    assert ran == ["gmail", "history"]  # each exactly once, however many intervals were missed
    note = next(r["payload"]["text"] for r in outbox_rows(database) if r["job_id"] is None)
    assert note == "While your computer was asleep: 1 sync skipped (each ran once to catch up)."


def test_nothing_missed_means_no_message(tmp_path: Path) -> None:
    database = Database(tmp_path / "c.db")
    clock = Clock()
    runner = make_runner(database, clock)
    worker = make_worker(database, clock, runner)
    worker.cycle(runner)
    clock.advance(hours=5)
    worker.cycle(runner)
    assert outbox_rows(database) == []


def test_on_start_a_missed_reminder_is_reported_as_not_running(tmp_path: Path) -> None:
    database = Database(tmp_path / "c.db")
    record_heartbeat(database, now=T0 - timedelta(hours=4))
    remind(database, T0 - timedelta(hours=2), "pay rent")
    clock = Clock()
    runner = make_runner(database, clock)
    worker = make_worker(database, clock, runner)
    worker.cycle(runner)
    note = next(r["payload"]["text"] for r in outbox_rows(database) if r["job_id"] is None)
    assert note == "While OpenDot was not running: 1 reminder (delivered late)."


def test_note_goes_to_the_ui_when_no_job_names_a_destination(tmp_path: Path) -> None:
    database = Database(tmp_path / "c.db")
    clock = Clock()
    catch_up = CatchUp(database, clock=clock)
    report = CatchUpReport(cause="wake", gap_seconds=3600, syncs_missed=2)
    assert catch_up.announce(report) is not None
    assert outbox_rows(database)[0]["destination"] == UI_DESTINATION
    assert "2 syncs skipped" in outbox_rows(database)[0]["payload"]["text"]


def test_keep_awake_failure_never_stops_the_cycle(tmp_path: Path) -> None:
    database = Database(tmp_path / "c.db")
    clock = Clock()
    runner = make_runner(database, clock)

    class Broken:
        def tick(self):  # noqa: ANN202
            raise RuntimeError("no power api")

        def close(self) -> None:
            return None

    worker = AlwaysOnWorker(database, keep_awake=Broken(), catch_up=CatchUp(database, clock=clock), clock=clock)  # type: ignore[arg-type]
    worker.cycle(runner)  # does not raise
    with database.connect() as connection:
        tools = [r["tool"] for r in connection.execute("SELECT tool FROM tool_runs").fetchall()]
    assert "keep_awake" in tools


def test_worker_thread_runs_the_loop_and_stops_cleanly(tmp_path: Path) -> None:
    from contextlib import contextmanager

    database = Database(tmp_path / "c.db")
    cycles: list[int] = []
    closed: list[bool] = []

    class FakeRunner:
        idle_sleep_seconds = 0.01
        connectors: tuple = ()

        def run_once(self) -> None:
            cycles.append(1)

        def mark_connectors_due(self) -> None:
            return None

        def handle_restart_request(self) -> bool:
            return False

    class Awake:
        def tick(self):  # noqa: ANN202
            return None

        def close(self) -> None:
            closed.append(True)

    @contextmanager
    def factory(db: Database):  # noqa: ANN202
        yield FakeRunner()

    worker = AlwaysOnWorker(database, runner_factory=factory, keep_awake=Awake(), catch_up=CatchUp(database))  # type: ignore[arg-type]
    worker.start()
    for _ in range(200):
        if len(cycles) >= 3:
            break
        import time as _time

        _time.sleep(0.01)
    worker.stop()
    assert len(cycles) >= 3 and closed == [True]


def test_keep_awake_class_is_wired_from_the_store(tmp_path: Path) -> None:
    from opendot_core.settings_store import SettingsStore

    database = Database(tmp_path / "c.db")
    worker = AlwaysOnWorker(database, catch_up=CatchUp(database))
    assert isinstance(worker.keep_awake, KeepAwake)
    assert worker.keep_awake.evaluate() is False  # off by default
    assert SettingsStore(database).get("keep_awake", dict, None) is None
