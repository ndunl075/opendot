import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from alfred.db import Database
from alfred.mcp_server import create_server
from alfred.policy import ApprovalService, PolicyStore


def test_mcp_server_can_be_constructed(tmp_path: Path) -> None:
    server = create_server(tmp_path / "alfred.db")

    assert server.name == "Alfred"


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
