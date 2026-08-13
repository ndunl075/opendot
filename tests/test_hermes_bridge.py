import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alfred.db import Database
from alfred.hermes_bridge import (
    TELEGRAM_MAX_MESSAGE_CHARS,
    AgentRunResult,
    HermesBridge,
    SubprocessAgentRunner,
)
from alfred.telegram import TelegramGateway, TelegramPair, TelegramUpdate


class FakeAgent:
    """Stands in for a Hermes turn; records prompts, returns a canned result."""

    def __init__(self, result: AgentRunResult) -> None:
        self.result = result
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> AgentRunResult:
        self.prompts.append(prompt)
        return self.result


def _update(update_id: int, text: str, *, chat_id: int = 20, user_id: int = 10, date: int = 0) -> TelegramUpdate:
    return TelegramUpdate.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id + 100,
                # Default to "now" so the bridge's lookback window includes it.
                "date": date or int(datetime.now(UTC).timestamp()),
                "chat": {"id": chat_id},
                "from": {"id": user_id},
                "text": text,
            },
        }
    )


def _defer(database_path: Path, update: TelegramUpdate) -> None:
    """Put one deferred message in the database the way real intake would."""
    TelegramGateway(
        Database(database_path),
        {TelegramPair(chat_id=20, user_id=10)},
        defer_unparsed_to_agent=True,
    ).handle(update)


def _replies(database_path: Path) -> list[tuple[str, str, str]]:
    with Database(database_path).connect() as connection:
        rows = connection.execute(
            "SELECT idempotency_key, destination, payload_json FROM outbox "
            "WHERE idempotency_key LIKE 'hermes-reply:%' ORDER BY idempotency_key"
        ).fetchall()
    return [(row["idempotency_key"], row["destination"], json.loads(row["payload_json"])["text"]) for row in rows]


def test_deferred_message_becomes_one_agent_reply_in_the_outbox(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _defer(database_path, _update(1, "what's on my agenda today?"))
    agent = FakeAgent(AgentRunResult(text="Your agenda is clear today.", ok=True))

    result = HermesBridge(Database(database_path), agent).run_once()

    assert (result.pending, result.answered, result.failed) == (1, 1, 0)
    assert agent.prompts == ["what's on my agenda today?"]
    assert _replies(database_path) == [("hermes-reply:1", "telegram:20", "Your agenda is clear today.")]


def test_running_twice_answers_once(tmp_path: Path) -> None:
    """The outbox key is the idempotency record -- a second pass must not pay
    for another model call or enqueue a duplicate reply."""
    database_path = tmp_path / "alfred.db"
    _defer(database_path, _update(2, "hello"))
    agent = FakeAgent(AgentRunResult(text="Hi.", ok=True))
    bridge = HermesBridge(Database(database_path), agent)

    bridge.run_once()
    second = bridge.run_once()

    assert (second.pending, second.answered) == (0, 0)
    assert len(agent.prompts) == 1
    assert len(_replies(database_path)) == 1


def test_a_recognized_command_is_never_sent_to_the_agent(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _defer(database_path, _update(3, "/task file taxes"))
    agent = FakeAgent(AgentRunResult(text="should not be called", ok=True))

    result = HermesBridge(Database(database_path), agent).run_once()

    assert (result.pending, result.answered) == (0, 0)
    assert agent.prompts == []
    assert _replies(database_path) == []


def test_messages_older_than_the_lookback_window_are_left_alone(tmp_path: Path) -> None:
    """Turning the bridge on must not fire a model call at every unanswered
    message ever received."""
    database_path = tmp_path / "alfred.db"
    old = int((datetime.now(UTC) - timedelta(hours=6)).timestamp())
    _defer(database_path, _update(4, "an old question", date=old))
    agent = FakeAgent(AgentRunResult(text="too late", ok=True))

    result = HermesBridge(Database(database_path), agent, lookback_seconds=900.0).run_once()

    assert (result.pending, result.answered) == (0, 0)
    assert agent.prompts == []


def test_a_failed_agent_turn_still_replies_and_audits_an_error(tmp_path: Path) -> None:
    """Fail closed and visibly: claim the key so an expensive call is not
    retried forever, and say so rather than leaving 'Thinking…' hanging."""
    database_path = tmp_path / "alfred.db"
    _defer(database_path, _update(5, "what's on my agenda?"))
    agent = FakeAgent(AgentRunResult(text="", ok=False, detail="agent timed out after 180s"))
    bridge = HermesBridge(Database(database_path), agent)

    result = bridge.run_once()

    assert (result.answered, result.failed) == (0, 1)
    assert _replies(database_path) == [("hermes-reply:5", "telegram:20", bridge.failure_reply)]
    with Database(database_path).connect() as connection:
        row = connection.execute(
            "SELECT outcome, result_json FROM tool_runs WHERE tool = 'hermes_bridge'"
        ).fetchone()
    assert row["outcome"] == "error"
    assert "timed out" in json.loads(row["result_json"])["detail"]

    # And it is not retried on the next pass.
    assert bridge.run_once().pending == 0
    assert len(agent.prompts) == 1


def test_a_reply_longer_than_telegrams_limit_is_truncated(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _defer(database_path, _update(6, "summarize everything"))
    agent = FakeAgent(AgentRunResult(text="x" * (TELEGRAM_MAX_MESSAGE_CHARS * 2), ok=True))

    HermesBridge(Database(database_path), agent).run_once()

    (_, _, text) = _replies(database_path)[0]
    assert len(text) <= TELEGRAM_MAX_MESSAGE_CHARS
    assert text.endswith("[truncated]")


def test_only_max_per_run_messages_are_answered_per_cycle(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    for update_id in (10, 11, 12):
        _defer(database_path, _update(update_id, f"question {update_id}"))
    agent = FakeAgent(AgentRunResult(text="answer", ok=True))

    result = HermesBridge(Database(database_path), agent, max_per_run=2).run_once()

    assert result.answered == 2
    assert len(_replies(database_path)) == 2


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_subprocess_runner_builds_the_documented_hermes_invocation() -> None:
    calls: list[tuple[list[str], dict]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _FakeCompleted(0, stdout="  the answer  \n")

    runner = SubprocessAgentRunner(command="hermes", profile="alfred", timeout_seconds=42.0, runner=fake_run)

    result = runner("what's on my agenda?")

    assert result.ok is True
    assert result.text == "the answer"  # stripped
    argv, kwargs = calls[0]
    assert argv == ["hermes", "-p", "alfred", "-z", "what's on my agenda?"]
    assert kwargs["timeout"] == 42.0
    # Windows would otherwise decode Hermes's em dashes and emoji with the ANSI codepage.
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["check"] is False


def test_subprocess_runner_reports_a_timeout_instead_of_raising() -> None:
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])

    result = SubprocessAgentRunner(profile="alfred", timeout_seconds=5.0, runner=fake_run)("hi")

    assert result.ok is False
    assert "timed out" in result.detail


def test_subprocess_runner_reports_a_missing_binary_instead_of_raising() -> None:
    def fake_run(argv, **kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    result = SubprocessAgentRunner(command="hermes", profile="alfred", runner=fake_run)("hi")

    assert result.ok is False
    assert "FileNotFoundError" in result.detail


def test_subprocess_runner_treats_a_nonzero_exit_and_empty_output_as_failures() -> None:
    def failing(argv, **kwargs):
        return _FakeCompleted(1, stdout="", stderr="boom")

    def silent(argv, **kwargs):
        return _FakeCompleted(0, stdout="   \n")

    failed = SubprocessAgentRunner(profile="alfred", runner=failing)("hi")
    assert failed.ok is False
    assert "exit 1" in failed.detail and "boom" in failed.detail

    empty = SubprocessAgentRunner(profile="alfred", runner=silent)("hi")
    assert empty.ok is False
    assert "no output" in empty.detail
