"""Scheduled work, as opposed to scheduled messages.

A reminder delivers text that already exists. "Check the order at 3 and text
me" has no text to deliver until the work runs, which is why it needs its own
job kind rather than a reminder with a clever wording.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alfred.db import Database
from alfred.hermes_bridge import AgentRunResult, HermesBridge
from alfred.jobs import JobRunner
from alfred.scheduled_tasks import ScheduledTaskStore


class _Agent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> AgentRunResult:
        self.prompts.append(prompt)
        return AgentRunResult(text="grandstand order is up.", ok=True, tool_count=2)

    def run_scoped(self, prompt: str, *, allowed_tools, **kwargs) -> AgentRunResult:
        return self(prompt)


def _schedule(
    database: Database,
    *,
    prompt: str,
    when: datetime,
    key: str = "k1",
    daily: bool = False,
    timezone_name: str | None = None,
):
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            return ScheduledTaskStore.schedule(
                connection,
                prompt=prompt,
                run_at=when,
                chat_id=20,
                daily=daily,
                timezone_name=timezone_name,
                idempotency_key=key,
            )


def _outbox_texts(database: Database) -> list[str]:
    with database.connect() as connection:
        rows = connection.execute("SELECT payload_json FROM outbox ORDER BY created_at").fetchall()
    return [json.loads(row["payload_json"])["text"] for row in rows]


def test_a_due_task_is_answered_and_delivered(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _schedule(database, prompt="check the grandstand order", when=datetime.now(UTC) - timedelta(minutes=1))

    JobRunner(database).run_due()
    agent = _Agent()
    HermesBridge(database, agent).run_once()

    assert agent.prompts, "the scheduled instruction never reached the agent"
    assert "check the grandstand order" in agent.prompts[0]
    assert _outbox_texts(database) == ["grandstand order is up."]


def test_the_delivered_message_says_nothing_about_a_job(tmp_path: Path) -> None:
    """The owner asked for an answer at three, not a status report."""
    database = Database(tmp_path / "alfred.db")
    task = _schedule(database, prompt="check the order", when=datetime.now(UTC) - timedelta(minutes=1))

    JobRunner(database).run_due()
    HermesBridge(database, _Agent()).run_once()

    delivered = " ".join(_outbox_texts(database)).lower()
    for leak in ("job", task.id, "cron", "schedule", "fired"):
        assert leak.lower() not in delivered, f"delivered message leaked {leak!r}"


def test_the_job_runner_does_not_run_the_agent_itself(tmp_path: Path) -> None:
    """An agent turn takes tens of seconds and opens this same database; doing
    it inside the job transaction would block the whole runner."""
    database = Database(tmp_path / "alfred.db")
    _schedule(database, prompt="check the order", when=datetime.now(UTC) - timedelta(minutes=1))

    executed = JobRunner(database).run_due()

    # Queued, not delivered: nothing exists to send until the bridge answers.
    assert len(executed) == 1
    assert executed[0].outbox_id is None
    assert _outbox_texts(database) == []


def test_a_future_task_does_not_fire_early(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _schedule(database, prompt="check later", when=datetime.now(UTC) + timedelta(hours=2))

    assert JobRunner(database).run_due() == []


def test_a_one_shot_task_does_not_repeat(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _schedule(database, prompt="check once", when=datetime.now(UTC) - timedelta(minutes=1))

    first = JobRunner(database).run_due()
    second = JobRunner(database).run_due()

    assert len(first) == 1
    assert second == []


def test_a_daily_task_reschedules_itself(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _schedule(
        database,
        prompt="check every morning",
        when=datetime.now(UTC) - timedelta(minutes=1),
        daily=True,
        timezone_name="America/New_York",
    )

    JobRunner(database).run_due()

    still_pending = ScheduledTaskStore.pending(database)
    assert len(still_pending) == 1
    assert still_pending[0].run_at > datetime.now(UTC)


def test_scheduling_the_same_thing_twice_is_one_job(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    when = datetime.now(UTC) + timedelta(hours=1)
    first = _schedule(database, prompt="check the order", when=when, key="same")
    second = _schedule(database, prompt="check the order", when=when, key="same")

    assert first.id == second.id
    assert len(ScheduledTaskStore.pending(database)) == 1


def test_a_scheduled_task_can_be_cancelled(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    task = _schedule(database, prompt="check the order", when=datetime.now(UTC) + timedelta(hours=1))

    assert ScheduledTaskStore.cancel(database, task.id) is True
    assert ScheduledTaskStore.pending(database) == []
    assert ScheduledTaskStore.cancel(database, task.id) is False


def test_an_empty_instruction_is_refused(tmp_path: Path) -> None:
    import pytest

    database = Database(tmp_path / "alfred.db")
    with pytest.raises(ValueError, match="instruction"):
        _schedule(database, prompt="   ", when=datetime.now(UTC) + timedelta(hours=1))


def test_a_daily_task_without_a_timezone_is_refused(tmp_path: Path) -> None:
    """Guessing the zone from a Windows tzinfo yields "Eastern Daylight Time",
    which is not loadable; a task silently pinned to the wrong zone is worse
    than one that refuses to be created."""
    import pytest

    database = Database(tmp_path / "alfred.db")
    with pytest.raises(ValueError, match="IANA timezone"):
        _schedule(
            database,
            prompt="check every morning",
            when=datetime.now(UTC).replace(tzinfo=UTC) + timedelta(hours=1),
            daily=True,
        )
