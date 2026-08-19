"""Startup records whether the agent can reach its tools.

The preflight command only helps if something runs it, and nobody runs a
diagnostic on a system that looks fine -- which is how three separate
outages survived for weeks while Alfred kept answering, just without tools.

Startup is the moment worth checking: it is when the Hermes profile has most
likely just been rewritten by an update that dropped the mcp_servers key.
"""

from __future__ import annotations

import json
from pathlib import Path

from alfred.db import Database
from alfred.mcp_server import MCP_TOOL_NAMES
from alfred.policy import PolicyStore
from alfred.runner import AlfredRunner


def _rows(database: Database) -> list[dict]:
    with database.connect() as connection:
        return [
            {"outcome": row["outcome"], "result": json.loads(row["result_json"] or "{}")}
            for row in connection.execute(
                "SELECT outcome, result_json FROM tool_runs WHERE tool = 'preflight'"
            ).fetchall()
        ]


def _runner(tmp_path: Path) -> AlfredRunner:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    return AlfredRunner(database, connectors=(), idle_sleep_seconds=0.0)


def test_a_broken_chain_is_audited_at_startup(tmp_path: Path) -> None:
    """No client granted anything, so nothing can be called."""
    runner = _runner(tmp_path)

    runner._audit_preflight()

    rows = _rows(runner.database)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "degraded"
    assert "mcp_tools_reachable" in rows[0]["result"]["broken"]


def test_a_healthy_chain_is_audited_too(tmp_path: Path) -> None:
    """A silent pass would leave no evidence the check ever ran, which is
    the state that let the outages hide."""
    runner = _runner(tmp_path)
    PolicyStore(runner.database).grant(
        client_id="hermes",
        allowed_sensitivities={"public", "personal"},
        allowed_tools=set(MCP_TOOL_NAMES),
        allow_write=True,
    )

    runner._audit_preflight()

    rows = _rows(runner.database)
    assert len(rows) == 1
    # Registration still fails under test (no Hermes profile on disk), so the
    # outcome is degraded -- what matters here is that a row exists at all.
    assert rows[0]["outcome"] in {"ok", "degraded"}


def test_a_broken_chain_never_stops_the_loop(tmp_path: Path) -> None:
    """Answering without tools is degraded but useful; refusing to start
    would turn a partial outage into a total one."""
    runner = _runner(tmp_path)

    runner.run_forever(iterations=1)

    assert _rows(runner.database), "startup still audited its own preflight"


def test_a_failing_diagnostic_is_not_fatal(tmp_path: Path, monkeypatch) -> None:
    """The check must never be the thing that stops the loop."""
    runner = _runner(tmp_path)

    def explode(*_args, **_kwargs):
        raise RuntimeError("preflight itself broke")

    monkeypatch.setattr("alfred.runner.preflight", explode)
    runner._audit_preflight()

    rows = _rows(runner.database)
    assert rows[0]["outcome"] == "error"
    assert rows[0]["result"]["error"] == "RuntimeError"
