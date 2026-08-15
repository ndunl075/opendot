import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from alfred.connector_records import ConnectorRecordStore
from alfred.db import Database
from alfred.gmail import _draft_message_id
from alfred.google_calendar import _calendar_event_id
from alfred.hermes_tools import HERMES_MCP_TOOL_FILTER_ENV
from alfred.mcp_server import MCP_TOOL_NAMES, create_server, main, parse_stdio_args
from alfred.policy import ApprovalService, PolicyStore


def test_mcp_server_can_be_constructed(tmp_path: Path) -> None:
    server = create_server(tmp_path / "alfred.db")

    assert server.name == "Alfred"
    assert {tool.name for tool in asyncio.run(server.list_tools())} == MCP_TOOL_NAMES


def test_mcp_server_registers_only_an_explicit_per_turn_tool_filter(tmp_path: Path) -> None:
    server = create_server(
        tmp_path / "alfred.db",
        client_id="hermes",
        tool_filter=frozenset({"agenda_get", "brief_get"}),
    )

    assert {tool.name for tool in asyncio.run(server.list_tools())} == {"agenda_get", "brief_get"}
    with pytest.raises(Exception, match="Unknown tool"):
        asyncio.run(server.call_tool("remember", {"statement": "must stay unavailable"}))

    empty_server = create_server(tmp_path / "empty.db", tool_filter=frozenset())
    assert asyncio.run(empty_server.list_tools()) == []


def test_mcp_server_rejects_an_unknown_filter_entry(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown MCP tool filter entries: invented_tool"):
        create_server(tmp_path / "alfred.db", tool_filter=frozenset({"invented_tool"}))


def test_parse_stdio_args_defaults_match_prior_hardcoded_behavior() -> None:
    """alfred-mcp with no arguments must behave exactly as it did before
    --client-id existed: 'local-mcp', the default database path."""
    args = parse_stdio_args([])

    assert (args.client_id, args.db) == ("local-mcp", None)


def test_parse_stdio_args_accepts_a_separate_client_id() -> None:
    """A second stdio client -- e.g. OpenAI's tunnel-client launched via its
    own --mcp-command -- gets its own identity instead of sharing
    Claude/Cursor's default local-mcp grant."""
    args = parse_stdio_args(["--client-id", "chatgpt-tunnel", "--db", "custom.db"])

    assert (args.client_id, args.db) == ("chatgpt-tunnel", "custom.db")


def test_main_builds_the_server_with_the_parsed_client_id_and_db() -> None:
    with (
        mock.patch("alfred.mcp_server.create_server") as create_server_mock,
        mock.patch.object(create_server_mock.return_value, "run") as run_mock,
    ):
        main(["--client-id", "chatgpt-tunnel", "--db", "custom.db"])

    create_server_mock.assert_called_once_with("custom.db", client_id="chatgpt-tunnel")
    run_mock.assert_called_once_with(transport="stdio")


def test_main_applies_the_inherited_hermes_tool_filter() -> None:
    with (
        mock.patch.dict(
            "os.environ",
            {HERMES_MCP_TOOL_FILTER_ENV: "brief_get, agenda_get"},
            clear=False,
        ),
        mock.patch("alfred.mcp_server.create_server") as create_server_mock,
        mock.patch.object(create_server_mock.return_value, "run") as run_mock,
    ):
        main(["--client-id", "hermes"])

    create_server_mock.assert_called_once_with(
        None,
        client_id="hermes",
        tool_filter=frozenset({"agenda_get", "brief_get"}),
    )
    run_mock.assert_called_once_with(transport="stdio")


def _call(server: Any, name: str, arguments: dict) -> Any:
    """Parse an MCP tool result regardless of whether FastMCP attached structured output."""
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple):
        return result[1]["result"] if set(result[1]) == {"result"} else result[1]
    content = result[0] if isinstance(result, list) else result
    return json.loads(content.text)


def _grant(database_path: Path, *, allow_write: bool = True, allowed_tools: set[str] | None = None) -> None:
    PolicyStore(Database(database_path)).grant(
        client_id="local-mcp",
        allowed_sensitivities={"public", "personal"},
        allowed_tools=allowed_tools
        or {"remember", "forget", "action_commit", "brief_get", "connector_status", "memory_search"},
        allow_write=allow_write,
    )


def test_remember_and_forget_round_trip_through_mcp(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(database_path)
    server = create_server(database_path)

    remembered = _call(server, "remember", {"statement": "Nico prefers a 7 AM brief."})
    assert remembered["statement"] == "Nico prefers a 7 AM brief."
    assert remembered["sensitivity"] == "personal"

    found = _call(server, "memory_search", {"query": "7 AM brief"})
    assert [memory["id"] for memory in found["memories"]] == [remembered["id"]]

    proposed = _call(server, "forget", {"memory_id": remembered["id"]})
    assert proposed["action_type"] == "memory_forget"
    assert proposed["state"] == "pending"

    # There is no MCP tool for approving: decision 8's "never unattended" is
    # only real if the same automated client can't both propose and approve.
    # A human (here, simulated directly through the policy layer, matching
    # what 'alfred approval-approve' does from the CLI) approves it instead.
    issued = ApprovalService(Database(database_path)).approve(proposed["id"], actor="mcp:local-mcp")

    receipt = _call(server, "action_commit", {"approval_id": proposed["id"], "token": issued.token})
    assert receipt == {
        "memory_id": remembered["id"],
        "idempotency_key": f"memory_forget:{proposed['id']}",
        "replayed": False,
    }

    after_forget = _call(server, "memory_search", {"query": "7 AM brief"})
    assert after_forget["memories"] == []


def test_memory_correction_and_feedback_round_trip_through_mcp(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(
        database_path,
        allowed_tools={"remember", "memory_search", "memory_correct", "memory_feedback"},
    )
    server = create_server(database_path)
    original = _call(server, "remember", {"statement": "Nico prefers long answers."})

    corrected = _call(
        server,
        "memory_correct",
        {"memory_id": original["id"], "replacement_statement": "Nico prefers concise answers."},
    )
    feedback = _call(
        server,
        "memory_feedback",
        {"memory_id": corrected["id"], "query": "response style", "outcome": "relevant"},
    )

    assert corrected["supersedes_memory_id"] == original["id"]
    assert feedback["outcome"] == "relevant"
    assert _call(server, "memory_search", {"query": "concise answers"})["memories"][0]["id"] == corrected["id"]


def test_action_commit_requires_its_own_grant_even_with_a_valid_token(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(database_path, allowed_tools={"remember", "forget", "memory_search"})  # no action_commit
    server = create_server(database_path)
    remembered = _call(server, "remember", {"statement": "Should stay if action_commit is blocked."})
    proposed = _call(server, "forget", {"memory_id": remembered["id"]})
    issued = ApprovalService(Database(database_path)).approve(proposed["id"], actor="mcp:local-mcp")

    with pytest.raises(Exception, match="not allowed"):
        asyncio.run(server.call_tool("action_commit", {"approval_id": proposed["id"], "token": issued.token}))

    still_there = _call(server, "memory_search", {"query": "action_commit is blocked"})
    assert len(still_there["memories"]) == 1


def test_remember_rejects_a_sensitivity_outside_the_client_scope(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(database_path)
    server = create_server(database_path)

    with pytest.raises(Exception, match="not scoped to write sensitivity"):
        asyncio.run(server.call_tool("remember", {"statement": "secret plan", "sensitivity": "secret"}))


def test_forget_rejects_a_memory_outside_the_client_scope(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    database = Database(database_path)
    from alfred.memory_graph import MemoryGraph

    sensitive = MemoryGraph(database).remember("Alfred project has private health notes.", sensitivity="sensitive")
    _grant(database_path)  # only public/personal
    server = create_server(database_path)

    with pytest.raises(Exception, match="not scoped to forget sensitivity"):
        asyncio.run(server.call_tool("forget", {"memory_id": sensitive.id}))


def test_a_write_scoped_client_cannot_remember_without_allow_write(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(database_path, allow_write=False)
    server = create_server(database_path)

    with pytest.raises(Exception, match="not allowed to write"):
        asyncio.run(server.call_tool("remember", {"statement": "should not be stored"}))


def test_brief_get_renders_on_demand(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(database_path)
    server = create_server(database_path)

    brief = _call(server, "brief_get", {})

    assert brief.startswith("Morning brief")


def test_connector_status_reports_sync_health_without_credentials(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    database = Database(database_path)
    database.migrate()
    # connector_health() compares against real wall-clock time (the MCP tool
    # takes no `now` override, by design), so this must stay recent relative
    # to whenever the test actually runs rather than a fixed historical date.
    recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                "INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at) "
                "VALUES ('github', 'self', NULL, ?, NULL, ?)",
                (recent, recent),
            )
    _grant(database_path)
    server = create_server(database_path)

    status = _call(server, "connector_status", {})

    assert len(status) == 1
    assert status[0]["connector"] == "github"
    assert status[0]["account"] == "self"
    assert status[0]["state"] == "ok"
    assert status[0]["last_error"] is None
    # Pydantic's JSON mode renders UTC as "Z"; compare the parsed instant, not the string form.
    assert datetime.fromisoformat(status[0]["last_success_at"]) == datetime.fromisoformat(recent)


def test_connector_records_get_filters_by_connector_and_record_type(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    database = Database(database_path)
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            ConnectorRecordStore.upsert(
                connection,
                connector="gmail",
                account="primary",
                record_type="unread_message",
                record_id="msg-1",
                payload={"subject": "Re: paper draft", "from": "advisor@school.edu", "snippet": "Looks good, but..."},
                active=True,
            )
            ConnectorRecordStore.upsert(
                connection,
                connector="gmail",
                account="primary",
                record_type="unread_message",
                record_id="msg-2",
                payload={"subject": "Newsletter", "from": "noreply@example.com", "snippet": "This week in..."},
                active=True,
            )
            ConnectorRecordStore.upsert(
                connection,
                connector="github",
                account="self",
                record_type="notification",
                record_id="notif-1",
                payload={"repository": "alfred", "reason": "mention"},
                active=True,
            )
    _grant(database_path, allowed_tools={"connector_records_get"})
    server = create_server(database_path)

    gmail_records = _call(server, "connector_records_get", {"connector": "gmail"})
    assert {record["record_id"] for record in gmail_records} == {"msg-1", "msg-2"}
    assert {record["payload"]["subject"] for record in gmail_records} == {"Re: paper draft", "Newsletter"}

    github_records = _call(
        server, "connector_records_get", {"connector": "github", "record_type": "notification"}
    )
    assert [record["record_id"] for record in github_records] == ["notif-1"]

    missing_type = _call(server, "connector_records_get", {"connector": "gmail", "record_type": "sent_message"})
    assert missing_type == []


def test_connector_records_get_excludes_inactive_records_and_respects_limit(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    database = Database(database_path)
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            for index in range(3):
                ConnectorRecordStore.upsert(
                    connection,
                    connector="gmail",
                    account="primary",
                    record_type="unread_message",
                    record_id=f"msg-{index}",
                    payload={"subject": f"Message {index}"},
                    active=True,
                )
            ConnectorRecordStore.upsert(
                connection,
                connector="gmail",
                account="primary",
                record_type="unread_message",
                record_id="msg-archived",
                payload={"subject": "Already read"},
                active=False,
            )
    _grant(database_path, allowed_tools={"connector_records_get"})
    server = create_server(database_path)

    limited = _call(server, "connector_records_get", {"connector": "gmail", "limit": 2})

    assert len(limited) == 2
    assert "msg-archived" not in {record["record_id"] for record in limited}


def test_connector_records_get_rejects_a_client_without_the_tool_grant(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(database_path, allowed_tools={"memory_search"})  # no connector_records_get
    server = create_server(database_path)

    with pytest.raises(Exception, match="not allowed"):
        asyncio.run(server.call_tool("connector_records_get", {"connector": "gmail"}))


def test_connector_records_get_enforces_connector_sensitivity(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    database = Database(database_path)
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            ConnectorRecordStore.replace_snapshot(
                connection,
                connector="google_health",
                account="self",
                record_type="sleep",
                records={"one": {"stage": "deep"}},
            )
    _grant(database_path, allowed_tools={"connector_records_get"})
    server = create_server(database_path)

    with pytest.raises(Exception, match="not scoped to read sensitive"):
        asyncio.run(server.call_tool("connector_records_get", {"connector": "google_health"}))


def test_hermes_raw_connector_results_use_the_same_pii_redaction_floor(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    database = Database(database_path)
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            ConnectorRecordStore.replace_snapshot(
                connection,
                connector="gmail",
                account="self",
                record_type="unread_message",
                records={"one": {"from": "person@example.com", "snippet": "call 513-555-1212"}},
            )
    PolicyStore(database).grant(
        client_id="hermes",
        allowed_sensitivities={"public", "personal"},
        allowed_tools={"connector_records_get"},
        allow_write=False,
    )
    server = create_server(database_path, client_id="hermes")

    records = _call(server, "connector_records_get", {"connector": "gmail"})

    serialized = json.dumps(records)
    assert "person@example.com" not in serialized
    assert "513-555-1212" not in serialized
    assert "[REDACTED:email]" in serialized


def test_task_upsert_creates_then_updates_without_clearing_the_due_date(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(database_path, allowed_tools={"task_upsert"})
    server = create_server(database_path)

    created = _call(server, "task_upsert", {"title": "Submit paper", "due_at": "2026-08-20T09:00:00-04:00"})
    assert (created["title"], created["state"]) == ("Submit paper", "open")

    updated = _call(server, "task_upsert", {"title": "Submit final paper", "task_id": created["id"]})
    assert updated["id"] == created["id"]
    assert updated["title"] == "Submit final paper"
    assert updated["due_at"] == created["due_at"]  # omitting due_at must not clear it


def test_task_complete_is_idempotent_through_mcp(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(database_path, allowed_tools={"task_upsert", "task_complete"})
    server = create_server(database_path)
    created = _call(server, "task_upsert", {"title": "Submit paper"})

    first = _call(server, "task_complete", {"task_id": created["id"]})
    second = _call(server, "task_complete", {"task_id": created["id"]})

    assert first["state"] == "completed"
    assert second["state"] == "completed"


def test_reminder_set_creates_its_own_task_when_none_is_given(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(database_path, allowed_tools={"reminder_set"})
    server = create_server(database_path)

    job = _call(
        server,
        "reminder_set",
        {"text": "Call advisor", "run_at": "2026-08-15T09:00:00-04:00", "chat_id": 20},
    )

    assert job["run_at"] == "2026-08-15T13:00:00Z"
    with Database(database_path).connect() as connection:
        row = connection.execute("SELECT title, state FROM tasks WHERE id = ?", (job["task_id"],)).fetchone()
    assert (row["title"], row["state"]) == ("Call advisor", "open")


class _FakeCalendarClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def close(self) -> None:
        pass

    def create_event(self, **kwargs: Any) -> dict:
        self.calls.append(kwargs)
        return {"id": f"created-{len(self.calls)}", "htmlLink": "https://calendar.google.com/event"}


def test_calendar_event_propose_never_touches_a_google_credential(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(database_path, allowed_tools={"calendar_event_propose"})
    server = create_server(database_path)

    with mock.patch("alfred.action_executor.current_access_token", side_effect=AssertionError("must not be called")):
        proposed = _call(
            server,
            "calendar_event_propose",
            {"summary": "Advisor meeting", "start": "2026-08-15T10:00:00-04:00", "end": "2026-08-15T11:00:00-04:00"},
        )

    assert proposed["action_type"] == "calendar_event_create"
    assert proposed["state"] == "pending"


def test_calendar_event_is_never_created_without_action_commit(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(database_path, allowed_tools={"calendar_event_propose", "action_commit"})
    server = create_server(database_path)

    proposed = _call(
        server,
        "calendar_event_propose",
        {"summary": "Advisor meeting", "start": "2026-08-15T10:00:00-04:00", "end": "2026-08-15T11:00:00-04:00"},
    )
    assert proposed["action_type"] == "calendar_event_create"
    assert proposed["state"] == "pending"

    issued = ApprovalService(Database(database_path)).approve(proposed["id"], actor="mcp:local-mcp")
    fake_client = _FakeCalendarClient()
    with (
        mock.patch("alfred.action_executor.current_access_token", return_value="FAKE_TOKEN"),
        mock.patch("alfred.action_executor.GoogleCalendarClient", return_value=fake_client),
    ):
        receipt = _call(server, "action_commit", {"approval_id": proposed["id"], "token": issued.token})

    assert receipt["calendar_event_id"] == "created-1"
    assert receipt["replayed"] is False
    assert fake_client.calls == [
        {
            "calendar_id": "primary",
            "event_id": _calendar_event_id(proposed["id"]),
            "summary": "Advisor meeting",
            "start": datetime(2026, 8, 15, 14, 0, tzinfo=UTC),
            "end": datetime(2026, 8, 15, 15, 0, tzinfo=UTC),
        }
    ]


def test_action_commit_replays_a_calendar_event_instead_of_creating_twice(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(database_path, allowed_tools={"calendar_event_propose", "action_commit"})
    server = create_server(database_path)
    proposed = _call(
        server,
        "calendar_event_propose",
        {"summary": "Advisor meeting", "start": "2026-08-15T10:00:00-04:00", "end": "2026-08-15T11:00:00-04:00"},
    )
    issued = ApprovalService(Database(database_path)).approve(proposed["id"], actor="mcp:local-mcp")
    fake_client = _FakeCalendarClient()

    with (
        mock.patch("alfred.action_executor.current_access_token", return_value="FAKE_TOKEN"),
        mock.patch("alfred.action_executor.GoogleCalendarClient", return_value=fake_client),
    ):
        first = _call(server, "action_commit", {"approval_id": proposed["id"], "token": issued.token})
        second = _call(server, "action_commit", {"approval_id": proposed["id"], "token": issued.token})

    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["calendar_event_id"] == first["calendar_event_id"]
    assert len(fake_client.calls) == 1


class _FakeGmailClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def close(self) -> None:
        pass

    def create_draft(self, **kwargs: Any) -> dict:
        self.calls.append(kwargs)
        return {"id": f"draft-{len(self.calls)}"}


def test_message_draft_never_creates_a_draft_without_action_commit(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(database_path, allowed_tools={"message_draft", "action_commit"})
    server = create_server(database_path)

    proposed = _call(
        server, "message_draft", {"to": "advisor@school.example", "subject": "Question", "body": "Quick question."}
    )
    assert proposed["action_type"] == "gmail_draft_create"
    assert proposed["state"] == "pending"

    issued = ApprovalService(Database(database_path)).approve(proposed["id"], actor="mcp:local-mcp")
    fake_client = _FakeGmailClient()
    with (
        mock.patch("alfred.action_executor.current_access_token", return_value="FAKE_TOKEN"),
        mock.patch("alfred.action_executor.GmailClient", return_value=fake_client),
    ):
        receipt = _call(server, "action_commit", {"approval_id": proposed["id"], "token": issued.token})

    assert receipt["draft_id"] == "draft-1"
    assert receipt["replayed"] is False
    assert fake_client.calls == [
        {
            "message_id": _draft_message_id(proposed["id"]),
            "to": "advisor@school.example",
            "subject": "Question",
            "body": "Quick question.",
        }
    ]


def test_action_commit_replays_a_gmail_draft_instead_of_creating_twice(tmp_path: Path) -> None:
    database_path = tmp_path / "alfred.db"
    _grant(database_path, allowed_tools={"message_draft", "action_commit"})
    server = create_server(database_path)
    proposed = _call(
        server, "message_draft", {"to": "advisor@school.example", "subject": "Question", "body": "Quick question."}
    )
    issued = ApprovalService(Database(database_path)).approve(proposed["id"], actor="mcp:local-mcp")
    fake_client = _FakeGmailClient()

    with (
        mock.patch("alfred.action_executor.current_access_token", return_value="FAKE_TOKEN"),
        mock.patch("alfred.action_executor.GmailClient", return_value=fake_client),
    ):
        first = _call(server, "action_commit", {"approval_id": proposed["id"], "token": issued.token})
        second = _call(server, "action_commit", {"approval_id": proposed["id"], "token": issued.token})

    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["draft_id"] == first["draft_id"]
    assert len(fake_client.calls) == 1


def _annotations(tmp_path: Path) -> dict[str, Any]:
    tools = asyncio.run(create_server(tmp_path / "alfred.db").list_tools())
    return {tool.name: tool.annotations for tool in tools}


def test_every_tool_is_annotated(tmp_path: Path) -> None:
    """An unannotated tool reads as "unknown" to a client, so one that would
    have paused before a destructive call has nothing to pause on."""
    annotations = _annotations(tmp_path)

    assert set(annotations) == set(MCP_TOOL_NAMES)
    assert all(value is not None for value in annotations.values())


def test_reads_are_marked_read_only_and_writes_are_not(tmp_path: Path) -> None:
    annotations = _annotations(tmp_path)
    reads = {
        "system_status",
        "agenda_get",
        "memory_search",
        "profile_get",
        "brief_get",
        "connector_status",
        "connector_records_get",
    }

    for name, annotation in annotations.items():
        assert annotation.readOnlyHint is (name in reads), name


def test_only_action_commit_is_destructive_or_reaches_a_provider(tmp_path: Path) -> None:
    """The propose/commit split is the whole safety design: a proposal writes
    an approval and contacts nobody, so calling it destructive would cry wolf
    on the one call that is actually safe to make."""
    annotations = _annotations(tmp_path)

    destructive = {name for name, a in annotations.items() if a.destructiveHint}
    open_world = {name for name, a in annotations.items() if a.openWorldHint}

    assert destructive == {"action_commit"}
    assert open_world == {"action_commit"}


def test_a_replayable_action_is_marked_idempotent(tmp_path: Path) -> None:
    annotations = _annotations(tmp_path)

    # action_commit replays its exact receipt rather than acting twice, and
    # completing an already-completed task is a documented no-op.
    assert annotations["action_commit"].idempotentHint is True
    assert annotations["task_complete"].idempotentHint is True
    # Each of these records something new every call.
    assert annotations["remember"].idempotentHint is False
    assert annotations["reminder_set"].idempotentHint is False


def test_read_only_tools_leave_destructive_unstated(tmp_path: Path) -> None:
    """destructiveHint is only meaningful for a tool that writes; stating it
    for a read implies it could destroy something."""
    annotations = _annotations(tmp_path)

    assert annotations["memory_search"].destructiveHint is None
    assert annotations["memory_search"].idempotentHint is None
