"""The check that would have caught eleven unreachable tools.

Alfred served 33 MCP tools and granted the agent 22. Every recently built
tool was routed to, offered, and then refused at the policy boundary. Nothing
reported it at any layer: `require_read` raises for the caller, the caller is
a language model, and the model apologised -- "the connector's not talking to
me right now" -- so the owner saw a plausible excuse instead of a
misconfiguration. Spot-checking made it worse, because `system_status` is the
one tool with no policy check and therefore always worked.
"""

from __future__ import annotations

import json
from pathlib import Path

from alfred.db import Database
from alfred.mcp_server import MCP_TOOL_NAMES
from alfred.policy import PolicyStore
from alfred.policy_coverage import PolicyCoverageService


def _report(database: Database):
    return PolicyCoverageService(database).report()


def _grant(database: Database, client_id: str, tools: set[str], *, active: bool = True) -> None:
    PolicyStore(database).grant(
        client_id=client_id,
        allowed_sensitivities={"public", "personal"},
        allowed_tools=tools,
        allow_write=True,
    )
    if not active:
        with database.connect() as connection:
            with database.transaction(connection):
                connection.execute(
                    "UPDATE client_scopes SET active = 0 WHERE client_id = ?", (client_id,)
                )


def test_a_fully_granted_client_leaves_nothing_unreachable(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    _grant(database, "hermes", set(MCP_TOOL_NAMES))

    report = _report(database)

    assert report.unreachable == []
    assert report.clients[0].missing == []


def test_the_real_failure_is_caught(tmp_path: Path) -> None:
    """Reconstructs the live state: the agent client granted everything except
    the tools built most recently."""
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    ungranted = {"availability_get", "threads_awaiting_reply", "pull_requests_get"}
    _grant(database, "hermes", set(MCP_TOOL_NAMES) - ungranted)

    report = _report(database)

    assert set(report.unreachable) == ungranted
    assert report.no_clients_registered is False


def test_a_narrow_second_client_does_not_raise_a_false_alarm(tmp_path: Path) -> None:
    """Section 7 wants per-client scopes so a coding client cannot read health
    data. A tool missing from one client is deliberate, and reporting it as a
    problem would make the check noise that nobody reads."""
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    _grant(database, "hermes", set(MCP_TOOL_NAMES))
    _grant(database, "cursor", {"agenda_get", "memory_search"})

    report = _report(database)

    assert report.unreachable == []
    narrow = next(client for client in report.clients if client.client_id == "cursor")
    assert narrow.missing, "a narrow grant is still reported as information"


def test_an_inactive_client_cannot_make_a_tool_reachable(tmp_path: Path) -> None:
    """A deactivated client calls nothing, so its grant must not count."""
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    _grant(database, "retired", set(MCP_TOOL_NAMES), active=False)

    report = _report(database)

    assert set(report.unreachable) == set(MCP_TOOL_NAMES)
    assert report.no_clients_registered is True


def test_no_clients_reads_as_unconfigured_not_drifted(tmp_path: Path) -> None:
    """A fresh install has granted nobody anything. That is setup pending, not
    a grant that fell behind, and the CLI must not fail a scheduled check for
    it."""
    database = Database(tmp_path / "alfred.db")
    database.migrate()

    report = _report(database)

    assert report.no_clients_registered is True
    assert set(report.unreachable) == set(MCP_TOOL_NAMES)


def test_a_tool_removed_from_the_server_is_reported_as_stale(tmp_path: Path) -> None:
    """The opposite drift: a grant naming a tool that no longer exists."""
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    _grant(database, "hermes", set(MCP_TOOL_NAMES) | {"tool_that_was_renamed"})

    report = _report(database)

    assert report.clients[0].stale == ["tool_that_was_renamed"]
    assert report.unreachable == []


def test_a_corrupt_grant_does_not_crash_the_report(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    _grant(database, "hermes", set(MCP_TOOL_NAMES))
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                "UPDATE client_scopes SET allowed_tools_json = '{not json' WHERE client_id = 'hermes'"
            )

    report = _report(database)

    assert set(report.unreachable) == set(MCP_TOOL_NAMES)


def test_the_report_names_tools_and_clients_only(tmp_path: Path) -> None:
    """Content-free like the rest of the reporting surface, so it is safe to
    paste into an issue."""
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    _grant(database, "hermes", {"agenda_get"})

    payload = json.loads(_report(database).model_dump_json())

    assert set(payload) == {"served", "clients", "unreachable", "no_clients_registered"}
    assert set(payload["clients"][0]) == {"client_id", "active", "granted", "missing", "stale"}
