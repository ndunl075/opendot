import json
from pathlib import Path

from alfred import turn_handshake
from alfred.hermes_tools import HERMES_MCP_TOOL_FILTER_ENV
from alfred.workflow_learning import WORKFLOW_TURN_ID_ENV


def _db(tmp_path: Path) -> Path:
    return tmp_path / "alfred.db"


def test_a_written_filter_is_read_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(HERMES_MCP_TOOL_FILTER_ENV, raising=False)
    turn_handshake.write(_db(tmp_path), turn_id="t1", tools=frozenset({"agenda_get", "brief_get"}))

    assert turn_handshake.read_tools(_db(tmp_path)) == frozenset({"agenda_get", "brief_get"})


def test_the_turn_id_is_read_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(WORKFLOW_TURN_ID_ENV, raising=False)
    turn_handshake.write(_db(tmp_path), turn_id="turn-42", tools=None)

    assert turn_handshake.read_turn_id(_db(tmp_path)) == "turn-42"


def test_no_tools_is_not_the_same_as_no_restriction(tmp_path: Path, monkeypatch) -> None:
    """The casual lane runs with zero tools. If an empty set collapsed to
    "unrestricted", a casual turn would be handed the entire tool surface --
    the exact bug this module exists to fix, in miniature."""
    monkeypatch.delenv(HERMES_MCP_TOOL_FILTER_ENV, raising=False)

    turn_handshake.write(_db(tmp_path), turn_id="t", tools=frozenset())
    assert turn_handshake.read_tools(_db(tmp_path)) == frozenset()

    turn_handshake.write(_db(tmp_path), turn_id="t", tools=None)
    assert turn_handshake.read_tools(_db(tmp_path)) is None


def test_the_environment_still_wins(tmp_path: Path, monkeypatch) -> None:
    """A direct alfred-mcp run (Claude, Cursor, the OpenAI tunnel) must behave
    exactly as before; only the Hermes path needs the file."""
    turn_handshake.write(_db(tmp_path), turn_id="t", tools=frozenset({"agenda_get"}))
    monkeypatch.setenv(HERMES_MCP_TOOL_FILTER_ENV, "brief_get")

    assert turn_handshake.read_tools(_db(tmp_path)) == frozenset({"brief_get"})


def test_nothing_published_means_unrestricted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(HERMES_MCP_TOOL_FILTER_ENV, raising=False)
    monkeypatch.delenv(WORKFLOW_TURN_ID_ENV, raising=False)

    assert turn_handshake.read_tools(_db(tmp_path)) is None
    assert turn_handshake.read_turn_id(_db(tmp_path)) is None


def test_the_handshake_is_removed_after_the_turn(tmp_path: Path, monkeypatch) -> None:
    """A stale file would let the next spawn inherit the previous turn's
    tools, which is how a casual turn silently gains write tools."""
    monkeypatch.delenv(HERMES_MCP_TOOL_FILTER_ENV, raising=False)
    with turn_handshake.published(_db(tmp_path), turn_id="t", tools=frozenset({"agenda_get"})) as path:
        assert path.exists()

    assert not turn_handshake.handshake_path(_db(tmp_path)).exists()
    assert turn_handshake.read_tools(_db(tmp_path)) is None


def test_cleanup_happens_even_when_the_turn_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(HERMES_MCP_TOOL_FILTER_ENV, raising=False)
    try:
        with turn_handshake.published(_db(tmp_path), turn_id="t", tools=frozenset({"agenda_get"})):
            raise RuntimeError("hermes exploded")
    except RuntimeError:
        pass

    assert not turn_handshake.handshake_path(_db(tmp_path)).exists()


def test_a_corrupt_handshake_falls_back_rather_than_failing(tmp_path: Path, monkeypatch) -> None:
    """An unreadable handshake must not take the turn down with it."""
    monkeypatch.delenv(HERMES_MCP_TOOL_FILTER_ENV, raising=False)
    path = turn_handshake.handshake_path(_db(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert turn_handshake.read_tools(_db(tmp_path)) is None
    assert turn_handshake.read_turn_id(_db(tmp_path)) is None


def test_clearing_an_absent_handshake_is_not_an_error(tmp_path: Path) -> None:
    turn_handshake.clear(_db(tmp_path))


def test_the_file_sits_beside_the_database(tmp_path: Path) -> None:
    """Derived from the database path rather than configured: a second
    setting is a second thing to get wrong, which is the failure this
    module repairs."""
    assert turn_handshake.handshake_path(_db(tmp_path)).parent == tmp_path


def test_the_payload_is_plain_json(tmp_path: Path) -> None:
    turn_handshake.write(_db(tmp_path), turn_id="t1", tools=frozenset({"b", "a"}))

    payload = json.loads(turn_handshake.handshake_path(_db(tmp_path)).read_text(encoding="utf-8"))

    assert payload == {"turn_id": "t1", "tools": ["a", "b"]}


def test_the_bridge_publishes_the_turn_it_is_running(tmp_path: Path, monkeypatch) -> None:
    """End to end through the bridge: whatever the runner sees on disk while
    Hermes is running is what that turn selected."""
    from alfred.db import Database
    from alfred.hermes_bridge import SubprocessAgentRunner

    monkeypatch.delenv(HERMES_MCP_TOOL_FILTER_ENV, raising=False)
    database = Database(_db(tmp_path))
    database.migrate()
    seen: dict = {}

    class _Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_runner(argv, **kwargs):
        seen["tools"] = turn_handshake.read_tools(database.path)
        seen["turn_id"] = turn_handshake.read_turn_id(database.path)
        return _Completed()

    runner = SubprocessAgentRunner(
        command="hermes", profile="alfred", database=database, runner=fake_runner
    )
    runner._run(
        "hello",
        allowed_tools=frozenset({"agenda_get"}),
        correlation_id="turn-77",
    )

    assert seen["tools"] == frozenset({"agenda_get"})
    assert seen["turn_id"] == "turn-77"
    # And it does not outlive the turn.
    assert not turn_handshake.handshake_path(database.path).exists()
