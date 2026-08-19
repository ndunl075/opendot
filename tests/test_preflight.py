"""Would the preflight have caught the failures that motivated it?

Each check here reconstructs a real breakage from this week rather than a
hypothetical, because the value of the command is entirely in whether it
speaks up when those recur.
"""

from __future__ import annotations

import json
from pathlib import Path

from alfred.db import Database
from alfred.mcp_server import MCP_TOOL_NAMES
from alfred.policy import PolicyStore
from alfred.preflight import preflight

REGISTERED = """\
providers:
  ollama-local:
    api_key: ""

mcp_servers:
  alfred:
    command: alfred-mcp
    args: [--client-id, hermes]
"""

# The live config before registration: the key present only as an example.
COMMENTED_ONLY = """\
providers:
  ollama-local:
    api_key: ""

# mcp_servers:
#   browseros-neo:
#     url: "http://127.0.0.1:9210/mcp"
"""


def _profile(tmp_path: Path, text: str, *, profile: str = "alfred") -> Path:
    root = tmp_path / "profiles"
    (root / profile).mkdir(parents=True, exist_ok=True)
    (root / profile / "config.yaml").write_text(text, encoding="utf-8")
    return root


def _granted(tmp_path: Path, tools: set[str]) -> Database:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    PolicyStore(database).grant(
        client_id="hermes",
        allowed_sensitivities={"public", "personal"},
        allowed_tools=tools,
        allow_write=True,
    )
    return database


def _check(report, name: str):
    return next(check for check in report.checks if check.name == name)


def test_a_healthy_install_passes(tmp_path: Path) -> None:
    database = _granted(tmp_path, set(MCP_TOOL_NAMES))

    report = preflight(database, profile_root=_profile(tmp_path, REGISTERED))

    assert report.ok is True
    assert all(check.fix is None for check in report.checks)


def test_an_unregistered_hermes_is_caught(tmp_path: Path) -> None:
    """The failure that started it: `hermes mcp list` said "No MCP servers
    configured" and nothing in Alfred ever asked."""
    database = _granted(tmp_path, set(MCP_TOOL_NAMES))

    report = preflight(database, profile_root=_profile(tmp_path, COMMENTED_ONLY))

    check = _check(report, "hermes_mcp_registered")
    assert report.ok is False
    assert check.ok is False
    assert check.fix == "alfred hermes-mcp-register"


def test_a_commented_example_does_not_count_as_registered(tmp_path: Path) -> None:
    """The live config carried a commented mcp_servers example, so a
    substring check would have reported success against the exact file that
    caused the outage."""
    database = _granted(tmp_path, set(MCP_TOOL_NAMES))

    report = preflight(database, profile_root=_profile(tmp_path, COMMENTED_ONLY))

    assert _check(report, "hermes_mcp_registered").ok is False


def test_a_drifted_grant_is_caught(tmp_path: Path) -> None:
    """The second failure: 22 tools granted against 33 served, so the rest
    were routed to, offered, and refused."""
    ungranted = {"availability_get", "threads_awaiting_reply", "pull_requests_get"}
    database = _granted(tmp_path, set(MCP_TOOL_NAMES) - ungranted)

    report = preflight(database, profile_root=_profile(tmp_path, REGISTERED))

    check = _check(report, "mcp_tools_reachable")
    assert report.ok is False
    assert "3 of" in check.detail
    for tool in sorted(ungranted)[:3]:
        assert tool in check.detail


def test_a_missing_profile_is_named_rather_than_guessed(tmp_path: Path) -> None:
    database = _granted(tmp_path, set(MCP_TOOL_NAMES))

    report = preflight(database, profile_root=tmp_path / "nothing-here")

    check = _check(report, "hermes_mcp_registered")
    assert check.ok is False
    assert "no Hermes profile config" in check.detail


def test_no_registered_client_reads_as_unconfigured(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()

    report = preflight(database, profile_root=_profile(tmp_path, REGISTERED))

    check = _check(report, "mcp_tools_reachable")
    assert check.ok is False
    assert "no active client" in check.detail


def test_every_failure_names_a_fix(tmp_path: Path) -> None:
    """A check that reports a problem without saying what to do about it is
    how a diagnostic becomes something people stop running."""
    database = Database(tmp_path / "alfred.db")
    database.migrate()

    report = preflight(database, profile_root=_profile(tmp_path, COMMENTED_ONLY))

    assert report.ok is False
    for check in report.checks:
        if not check.ok:
            assert check.fix, check.name


def test_the_report_is_safe_to_paste(tmp_path: Path) -> None:
    """Configuration state only: no addresses, tokens, or message content."""
    database = _granted(tmp_path, set(MCP_TOOL_NAMES))

    payload = json.loads(
        preflight(database, profile_root=_profile(tmp_path, REGISTERED)).model_dump_json()
    )

    assert set(payload) == {"ok", "checks"}
    assert set(payload["checks"][0]) == {"name", "ok", "detail", "fix"}
