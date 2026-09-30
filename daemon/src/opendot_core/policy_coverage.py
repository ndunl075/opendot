"""Which MCP tools no client can actually reach.

Alfred serves 33 tools and grants them per client. Nothing ever compared the
two lists, and the gap that opened was invisible for weeks: the `hermes`
client -- the only one the agent uses -- was granted 22 while the server
served 33. Every tool built recently was selected by the router, offered to
the model, and then refused at the policy boundary.

Nothing reported it, at any layer. `policy.require_read` raises for the
caller, and the caller is a language model, which apologised and moved on:
"the connector's not talking to me right now". The owner saw a plausible
excuse rather than a misconfiguration, and `system_status` happened to be
the one tool with no policy check, so spot-checking it suggested everything
was fine.

**A narrow grant is not a bug.** Section 7 wants per-client scopes precisely
so a coding client cannot read health data or send personal messages. So a
tool missing from *one* client is deliberate and reported as information
only. The finding worth acting on is a tool granted to **nobody**: served,
routable, and reachable by no client at all, which is only ever an oversight
because nothing can use it.

Read-only and content-free, like the rest of the reporting surface: tool
names and client ids, no arguments and no data.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from .db import Database
from .mcp_server import MCP_TOOL_NAMES


class ClientCoverage(BaseModel):
    client_id: str
    active: bool
    granted: int
    #: Served but not granted to this client. Often deliberate scoping.
    missing: list[str] = Field(default_factory=list)
    #: Granted but no longer served -- a tool that was renamed or removed and
    #: left behind in the grant.
    stale: list[str] = Field(default_factory=list)


class PolicyCoverageReport(BaseModel):
    served: int
    clients: list[ClientCoverage] = Field(default_factory=list)
    #: Served tools granted to no active client. The actionable list: nothing
    #: can call these, so the router can offer them and the model will be
    #: refused every time.
    unreachable: list[str] = Field(default_factory=list)
    #: True when no client is registered at all, in which case `unreachable`
    #: is everything and means "not configured yet" rather than "drifted".
    no_clients_registered: bool = False


class PolicyCoverageService:
    """Compare the tools the MCP server serves against what clients may call."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def report(self) -> PolicyCoverageReport:
        self.database.migrate()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT client_id, active, allowed_tools_json FROM client_scopes "
                "ORDER BY client_id"
            ).fetchall()

        served = set(MCP_TOOL_NAMES)
        clients: list[ClientCoverage] = []
        reachable: set[str] = set()
        for row in rows:
            try:
                granted = {str(name) for name in json.loads(row["allowed_tools_json"])}
            except (TypeError, ValueError):
                granted = set()
            active = bool(row["active"])
            if active:
                reachable |= granted
            clients.append(
                ClientCoverage(
                    client_id=str(row["client_id"]),
                    active=active,
                    granted=len(granted),
                    missing=sorted(served - granted),
                    stale=sorted(granted - served),
                )
            )
        return PolicyCoverageReport(
            served=len(served),
            clients=clients,
            unreachable=sorted(served - reachable),
            no_clients_registered=not any(client.active for client in clients),
        )
