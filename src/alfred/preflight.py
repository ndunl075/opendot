"""Can the agent actually reach Alfred's tools, end to end?

Three separate failures this week each made Alfred silently toolless, and
each was invisible for weeks because every layer worked in isolation:

- Hermes had no MCP server registered at all, so 33 tools were never
  offered. `hermes mcp list` said so plainly; nothing ever asked it.
- The policy grant drifted to 22 tools against 33 served, so the rest were
  routed to, offered, and refused at the boundary.
- The per-turn tool filter never crossed into the MCP subprocess, so the
  filter was inert.

None of them raised anything. `require_read` raises for its caller, the
caller is a language model, and a model that cannot reach a tool apologises
and moves on -- "the connector's not talking to me right now" -- which reads
to the owner as a transient outage rather than a broken install. The chain is
only as good as its weakest link and no single component could see the chain.

So this checks the links themselves, in order, and says which one is broken
and what fixes it. Read-only: it inspects configuration and grants, calls no
provider, and changes nothing.

Registration is the check worth having most, because it is the one that
un-fixes itself: `hermes profile update` rewrites the profile config, and
nothing in Alfred would notice the key going missing again.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .db import Database
from .hermes_mcp import is_registered, profile_config_path
from .policy_coverage import PolicyCoverageService


class Check(BaseModel):
    name: str
    ok: bool
    detail: str
    #: The command that repairs it, when there is one. Printed rather than
    #: run: a preflight that fixes things is a preflight nobody reads.
    fix: str | None = None


class PreflightReport(BaseModel):
    ok: bool
    checks: list[Check] = Field(default_factory=list)


def preflight(
    database: Database,
    *,
    profile: str = "alfred",
    profile_root: Path | None = None,
) -> PreflightReport:
    checks = [
        _hermes_registration(profile=profile, profile_root=profile_root),
        _tool_reachability(database),
    ]
    return PreflightReport(ok=all(check.ok for check in checks), checks=checks)


def _hermes_registration(*, profile: str, profile_root: Path | None) -> Check:
    path = profile_config_path(profile, profile_root=profile_root)
    name = "hermes_mcp_registered"
    if not path.exists():
        return Check(
            name=name,
            ok=False,
            detail=f"no Hermes profile config at {path}",
            fix="install the Hermes profile, then run: alfred hermes-mcp-register",
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return Check(name=name, ok=False, detail=f"cannot read {path}: {error.__class__.__name__}")
    if not is_registered(text):
        return Check(
            name=name,
            ok=False,
            detail=(
                "Hermes has no mcp_servers key, so Alfred's tools are invisible to the "
                "agent and every answer comes from the prompt context alone"
            ),
            fix="alfred hermes-mcp-register",
        )
    return Check(name=name, ok=True, detail="Hermes is configured to start Alfred's MCP server")


def _tool_reachability(database: Database) -> Check:
    coverage = PolicyCoverageService(database).report()
    name = "mcp_tools_reachable"
    if coverage.no_clients_registered:
        return Check(
            name=name,
            ok=False,
            detail="no active client is registered, so no tool can be called",
            fix="alfred client-grant --client-id hermes --tool <name> --allow-write",
        )
    if coverage.unreachable:
        shown = ", ".join(coverage.unreachable[:5])
        more = "" if len(coverage.unreachable) <= 5 else f" (+{len(coverage.unreachable) - 5} more)"
        return Check(
            name=name,
            ok=False,
            detail=(
                f"{len(coverage.unreachable)} of {coverage.served} served tools are granted to "
                f"no active client and will be refused when called: {shown}{more}"
            ),
            fix="alfred policy-coverage, then grant the missing tools",
        )
    return Check(
        name=name,
        ok=True,
        detail=f"all {coverage.served} served tools are reachable by an active client",
    )
