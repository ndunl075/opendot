import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from alfred.db import Database
from alfred.mcp_server import create_server
from alfred.policy import PolicyStore


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


def _grant(database_path: Path, *, allow_write: bool = True) -> None:
    PolicyStore(Database(database_path)).grant(
        client_id="local-mcp",
        allowed_sensitivities={"public", "personal"},
        allowed_tools={"remember", "forget", "brief_get", "connector_status", "memory_search"},
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

    forgotten = _call(server, "forget", {"memory_id": remembered["id"]})
    assert forgotten["status"] == "deleted"

    after_forget = _call(server, "memory_search", {"query": "7 AM brief"})
    assert after_forget["memories"] == []


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
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                "INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at) "
                "VALUES ('github', 'self', NULL, '2026-08-11T09:00:00+00:00', NULL, '2026-08-11T09:00:00+00:00')"
            )
    _grant(database_path)
    server = create_server(database_path)

    status = _call(server, "connector_status", {})

    assert status == [
        {
            "connector": "github",
            "account": "self",
            "last_success_at": "2026-08-11T09:00:00+00:00",
            "last_error": None,
            "updated_at": "2026-08-11T09:00:00+00:00",
        }
    ]
