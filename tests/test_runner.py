from pathlib import Path
from threading import Event, Thread
import time

from alfred.audit import AuditLog
from alfred.db import Database
from alfred.outbox import Outbox
from alfred.runner import AlfredRunner, ConnectorSync
from alfred.telegram import TelegramPair


class FakeTelegram:
    def __init__(self, updates: list[dict] | None = None) -> None:
        self.updates = updates or []
        self.sent: list[tuple[int, str]] = []
        self.chat_actions: list[tuple[int, str]] = []
        self.polls = 0

    def get_updates(self, *, offset: int | None, timeout_seconds: int = 25) -> list[dict]:
        self.polls += 1
        return [update for update in self.updates if offset is None or update.get("update_id", -1) >= offset]

    def send_message(self, *, chat_id: int, text: str, reply_markup: dict | None = None) -> int:
        self.sent.append((chat_id, text))
        return 1

    def send_chat_action(self, *, chat_id: int, action: str = "typing") -> None:
        self.chat_actions.append((chat_id, action))

    def answer_callback_query(self, *, callback_query_id: str, text: str) -> None:
        return None


def _reminder_update() -> dict:
    # A reminder time well in the past means the scheduled job is already
    # due, so one run_once() cycle carries it from intake through delivery.
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 1_786_198_400,
            "chat": {"id": 20},
            "from": {"id": 10},
            "text": "/remind 2020-01-01T09:00:00Z submit paper",
        },
    }


def test_run_once_carries_a_reminder_from_intake_through_delivery(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    fake = FakeTelegram([_reminder_update()])
    runner = AlfredRunner(
        database,
        telegram_transport=fake,
        telegram_pairs=frozenset({TelegramPair(chat_id=20, user_id=10)}),
        telegram_chat_ids=frozenset({20}),
    )

    report = runner.run_once()

    assert report.telegram_polled is True
    assert report.errors == []
    assert report.jobs_executed == 1
    # The immediate "saved" receipt and the already-due reminder both deliver in this cycle.
    # Both land in the same outbox second, so delivery order between them is not guaranteed.
    assert report.telegram_delivered == 2
    assert sorted(fake.sent) == sorted(
        [
            (20, "Saved reminder for 2020-01-01T09:00:00+00:00: submit paper"),
            (20, "Late reminder (scheduled 2020-01-01T09:00:00+00:00): submit paper"),
        ]
    )
    assert AuditLog(database).verify() is True


def test_one_cycle_polls_answers_and_delivers_an_agent_reply(tmp_path: Path) -> None:
    """The agent step runs before delivery on purpose. As a connector it ran
    after, which stranded every answer in the outbox for a whole extra cycle
    (measured at 26s of pure latency against a real Telegram round trip)."""
    from datetime import UTC, datetime

    from alfred.hermes_bridge import AgentRunResult, HermesBridge

    database = Database(tmp_path / "alfred.db")
    free_form = {
        "update_id": 2,
        "message": {
            "message_id": 2,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": 20},
            "from": {"id": 10},
            "text": "what's on my agenda today?",
        },
    }
    fake = FakeTelegram([free_form])
    bridge = HermesBridge(database, lambda prompt: AgentRunResult(text="should not be called", ok=True))
    runner = AlfredRunner(
        database,
        telegram_transport=fake,
        telegram_pairs=frozenset({TelegramPair(chat_id=20, user_id=10)}),
        telegram_chat_ids=frozenset({20}),
        defer_unparsed_to_agent=True,
        agent_bridge=bridge.run_once,
    )

    report = runner.run_once()

    assert report.errors == []
    assert report.agent_replies == 1
    # The acknowledgement and both conversational answer bubbles leave in
    # this same cycle. The ack is matched by keyword at intake (no model call).
    assert fake.sent == [
        (20, "checking your agenda..."),
        (
            20,
            "I only have your primary calendar synced right now, so I can't reliably say "
            "whether your full Google Calendar is clear.",
        ),
        (20, "want me to check the calendar connection?"),
    ]
    assert fake.chat_actions == []


def test_casual_message_goes_straight_to_the_agent_without_a_queue_ack(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from alfred.hermes_bridge import AgentRunResult, HermesBridge

    database = Database(tmp_path / "alfred.db")
    fake = FakeTelegram(
        [
            {
                "update_id": 3,
                "message": {
                    "message_id": 3,
                    "date": int(datetime.now(UTC).timestamp()),
                    "chat": {"id": 20},
                    "from": {"id": 10},
                    "text": "yo",
                },
            }
        ]
    )
    bridge = HermesBridge(database, lambda prompt: AgentRunResult(text="yo. what's good?", ok=True))
    report = AlfredRunner(
        database,
        telegram_transport=fake,
        telegram_pairs=frozenset({TelegramPair(chat_id=20, user_id=10)}),
        telegram_chat_ids=frozenset({20}),
        defer_unparsed_to_agent=True,
        agent_bridge=bridge.run_once,
    ).run_once()

    assert report.errors == []
    assert report.agent_replies == 1
    assert fake.sent == [(20, "yo. what's good?")]
    assert fake.chat_actions == [(20, "typing")]


def test_typing_status_is_refreshed_until_the_agent_finishes(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    fake = FakeTelegram()
    agent_started = Event()
    release_agent = Event()
    reports: list[object] = []

    def slow_agent_bridge():
        agent_started.set()
        assert release_agent.wait(timeout=2.0)
        return type("Answer", (), {"answered": 1})()

    runner = AlfredRunner(
        database,
        telegram_transport=fake,
        telegram_chat_ids=frozenset({20}),
        agent_bridge=slow_agent_bridge,
        agent_typing_chat_ids=lambda: frozenset({20}),
        typing_heartbeat_interval_seconds=0.01,
    )
    worker = Thread(target=lambda: reports.append(runner.run_once()))

    worker.start()
    assert agent_started.wait(timeout=1.0)
    deadline = time.monotonic() + 1.0
    while len(fake.chat_actions) < 3 and time.monotonic() < deadline:
        time.sleep(0.005)
    release_agent.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert len(fake.chat_actions) >= 3
    assert set(fake.chat_actions) == {(20, "typing")}
    stopped_count = len(fake.chat_actions)
    time.sleep(0.03)
    assert len(fake.chat_actions) == stopped_count
    assert reports[0].errors == []


def test_typing_heartbeat_failure_never_blocks_the_agent(tmp_path: Path) -> None:
    class BrokenTypingTelegram(FakeTelegram):
        def send_chat_action(self, *, chat_id: int, action: str = "typing") -> None:
            raise TimeoutError("cosmetic request failed")

    called: list[str] = []
    report = AlfredRunner(
        Database(tmp_path / "alfred.db"),
        telegram_transport=BrokenTypingTelegram(),
        agent_bridge=lambda: called.append("agent") or type("Answer", (), {"answered": 1})(),
        agent_typing_chat_ids=lambda: frozenset({20}),
        typing_heartbeat_interval_seconds=0.01,
    ).run_once()

    assert called == ["agent"]
    assert report.agent_replies == 1
    assert report.errors == []


def test_run_once_skips_telegram_entirely_when_not_configured(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    runner = AlfredRunner(database)

    report = runner.run_once()

    assert report.telegram_polled is False
    assert report.telegram_delivered == 0
    assert report.errors == []


def test_memory_learning_runs_after_the_agent_reply(tmp_path: Path) -> None:
    calls: list[str] = []

    class Learned:
        promoted = 1

    runner = AlfredRunner(
        Database(tmp_path / "alfred.db"),
        agent_bridge=lambda: calls.append("answer") or type("Answer", (), {"answered": 1})(),
        memory_learning=lambda: calls.append("learn") or Learned(),
    )

    report = runner.run_once()

    assert calls == ["answer", "learn"]
    assert report.memories_learned == 1


def test_connector_sync_runs_once_per_configured_interval(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    calls: list[int] = []
    clock = {"t": 0.0}
    connector = ConnectorSync(name="canvas", interval_seconds=100, run=lambda: calls.append(1))
    runner = AlfredRunner(database, connectors=(connector,), now=lambda: clock["t"])

    first = runner.run_once()
    clock["t"] = 50
    second = runner.run_once()
    clock["t"] = 150
    third = runner.run_once()

    assert first.connectors_synced == ["canvas"]
    assert second.connectors_synced == []
    assert third.connectors_synced == ["canvas"]
    assert len(calls) == 2


def test_background_connector_batch_does_not_block_telegram_cycles(tmp_path: Path) -> None:
    started = Event()
    release = Event()
    completed = Event()

    def slow_sync() -> None:
        started.set()
        release.wait(timeout=2)
        completed.set()

    fake = FakeTelegram()
    runner = AlfredRunner(
        Database(tmp_path / "alfred.db"),
        telegram_transport=fake,
        telegram_pairs=frozenset({TelegramPair(chat_id=20, user_id=10)}),
        telegram_chat_ids=frozenset({20}),
        connectors=(ConnectorSync(name="slow", interval_seconds=900, run=slow_sync),),
        background_connectors=True,
    )

    first = runner.run_once()
    assert started.wait(timeout=1)
    second = runner.run_once()

    assert first.connectors_synced == []
    assert second.connectors_synced == []
    assert fake.polls == 2

    release.set()
    assert completed.wait(timeout=1)
    third = runner.run_once()

    assert third.connectors_synced == ["slow"]


def test_a_failing_connector_does_not_stop_the_loop_or_other_connectors(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    calls: list[str] = []

    def failing() -> None:
        raise RuntimeError("boom")

    def working() -> None:
        calls.append("worked")

    runner = AlfredRunner(
        database,
        connectors=(
            ConnectorSync(name="broken", interval_seconds=0, run=failing),
            ConnectorSync(name="fine", interval_seconds=0, run=working),
        ),
    )

    report = runner.run_once()

    assert calls == ["worked"]
    assert report.connectors_synced == ["fine"]
    assert len(report.errors) == 1
    assert "broken" in report.errors[0]
    assert "boom" in report.errors[0]
    assert AuditLog(database).verify() is True


def test_failing_connector_uses_exponential_backoff_instead_of_every_cycle(tmp_path: Path) -> None:
    clock = {"now": 0.0}
    calls: list[float] = []

    def failing() -> None:
        calls.append(clock["now"])
        raise RuntimeError("offline")

    runner = AlfredRunner(
        Database(tmp_path / "alfred.db"),
        connectors=(ConnectorSync(name="offline", interval_seconds=900, run=failing),),
        now=lambda: clock["now"],
    )

    runner.run_once()
    clock["now"] = 5
    runner.run_once()
    clock["now"] = 30
    runner.run_once()
    clock["now"] = 60
    runner.run_once()
    clock["now"] = 90
    runner.run_once()

    assert calls == [0.0, 30, 90]


def test_run_forever_stops_after_the_configured_iteration_count(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    sleeps: list[float] = []
    runner = AlfredRunner(database, idle_sleep_seconds=7, sleep=sleeps.append)

    runner.run_forever(iterations=3)

    # Slept between cycles but not after the final one.
    assert sleeps == [7, 7]


def test_run_forever_retries_a_failed_telegram_poll_after_one_second(tmp_path: Path) -> None:
    class OfflineTelegram(FakeTelegram):
        def get_updates(self, *, offset: int | None, timeout_seconds: int = 25) -> list[dict]:
            raise TimeoutError("offline")

    sleeps: list[float] = []
    runner = AlfredRunner(
        Database(tmp_path / "alfred.db"),
        telegram_transport=OfflineTelegram(),
        telegram_pairs=frozenset({TelegramPair(chat_id=20, user_id=10)}),
        idle_sleep_seconds=5.0,
        sleep=sleeps.append,
    )

    runner.run_forever(iterations=2)

    assert sleeps == [1.0]


def test_run_forever_stops_when_stop_check_reports_true(tmp_path: Path) -> None:
    """The Windows service (and anything else supervising the loop
    externally) stops it this way instead of relying on iterations or
    KeyboardInterrupt."""
    database = Database(tmp_path / "alfred.db")
    sleeps: list[float] = []
    cycles = {"count": 0}

    def stop_after_two_cycles() -> bool:
        return cycles["count"] >= 2

    connector = ConnectorSync(name="counter", interval_seconds=0, run=lambda: cycles.__setitem__("count", cycles["count"] + 1))
    runner = AlfredRunner(database, connectors=(connector,), sleep=sleeps.append)

    runner.run_forever(stop_check=stop_after_two_cycles)

    assert cycles["count"] == 2
    assert sleeps == [5.0]  # slept once, between the two cycles; the stop check then skipped a third


def test_run_forever_never_stops_on_its_own_without_a_stop_check_or_iterations(tmp_path: Path) -> None:
    """Confirms the default stop_check is a true no-op, not an accidental early exit."""
    database = Database(tmp_path / "alfred.db")
    sleeps: list[float] = []
    runner = AlfredRunner(database, sleep=sleeps.append)

    runner.run_forever(iterations=5, stop_check=lambda: False)

    assert len(sleeps) == 4


def test_pending_reminder_is_delivered_even_without_a_new_telegram_message(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            Outbox.enqueue(
                connection, destination="telegram:20", payload={"text": "already due"}, idempotency_key="preexisting"
            )
    fake = FakeTelegram()
    runner = AlfredRunner(database, telegram_transport=fake, telegram_chat_ids=frozenset({20}))

    report = runner.run_once()

    assert report.telegram_delivered == 1
    assert fake.sent == [(20, "already due")]
