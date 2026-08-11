"""Narrow stdio MCP surface, growing toward ARCHITECTURE.md section 7."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import cast

from mcp.server.fastmcp import FastMCP

from .briefing import BriefingService
from .config import Settings
from .connector_health import connector_health
from .db import Database
from .memory_graph import GraphError, MemoryGraph, Sensitivity
from .policy import PolicyError, PolicyStore

ALLOWED_SENSITIVITIES: frozenset[str] = frozenset({"public", "personal", "sensitive", "secret"})


def create_server(database_path: Path | str | None = None, *, client_id: str = "local-mcp") -> FastMCP:
    """Create Alfred's MCP server: local memory reads/writes and connector status.

    Every tool is gated by PolicyStore, so an unregistered or narrowly scoped
    client gets nothing by default. Consequential external actions (calendar
    writes, sending messages) are not exposed here yet -- action_commit and
    message_draft need their own careful pass at how a stateless MCP tool
    call should present a two-step propose/approve/execute flow.
    """
    settings = Settings.from_environment(Path(database_path) if database_path else None)
    database = Database(settings.database_path)
    policy = PolicyStore(database)
    server = FastMCP("Alfred")

    @server.tool()
    def system_status() -> dict[str, int | str]:
        """Return Alfred's non-sensitive local health and schema status."""
        return database.status()

    @server.tool()
    def agenda_get() -> str:
        """Return Alfred's deterministic local task agenda with freshness."""
        return BriefingService(database).morning_brief().render()

    @server.tool()
    def memory_search(query: str) -> dict:
        """Search local memory anchors and their one-hop active graph context."""
        scope = policy.require_read(client_id, "memory_search")
        return MemoryGraph(database).search(query, allowed_sensitivities=scope.allowed_sensitivities).model_dump(mode="json")

    @server.tool()
    def profile_get() -> dict:
        """Return the local owner node and current, evidence-backed profile relationships."""
        scope = policy.require_read(client_id, "profile_get")
        owner, relationships = MemoryGraph(database).profile(allowed_sensitivities=scope.allowed_sensitivities)
        return {
            "owner": owner.model_dump(mode="json") if owner else None,
            "relationships": [relationship.model_dump(mode="json") for relationship in relationships],
        }

    @server.tool()
    def remember(statement: str, kind: str = "note", sensitivity: str = "personal") -> dict:
        """Store a confirmed local memory; the calling client is recorded as actor."""
        if sensitivity not in ALLOWED_SENSITIVITIES:
            raise ValueError(f"unknown sensitivity: {sensitivity}")
        scope = policy.require_write(client_id, "remember")
        if sensitivity not in scope.allowed_sensitivities:
            raise PolicyError(f"client is not scoped to write sensitivity: {sensitivity}")
        memory = MemoryGraph(database).remember(
            statement, kind=kind, sensitivity=cast(Sensitivity, sensitivity), actor=f"mcp:{client_id}"
        )
        return memory.model_dump(mode="json")

    @server.tool()
    def forget(memory_id: str, reason: str = "user requested deletion") -> dict:
        """Scoped deletion of one memory the caller can see; evidence and audit are kept."""
        scope = policy.require_write(client_id, "forget")
        graph = MemoryGraph(database)
        existing = graph.get_memory(memory_id)
        if existing is None:
            raise GraphError(f"memory does not exist: {memory_id}")
        if existing.sensitivity not in scope.allowed_sensitivities:
            raise PolicyError(f"client is not scoped to forget sensitivity: {existing.sensitivity}")
        memory = graph.forget_memory(memory_id, reason=reason, actor=f"mcp:{client_id}")
        return memory.model_dump(mode="json")

    @server.tool()
    def brief_get(now: str | None = None) -> str:
        """Render the deterministic local morning brief on demand, not just on schedule."""
        policy.require_read(client_id, "brief_get")
        parsed = datetime.fromisoformat(now) if now else None
        return BriefingService(database).morning_brief(parsed).render()

    @server.tool()
    def connector_status() -> list[dict]:
        """Report each connector's health; never its credentials or synced content."""
        policy.require_read(client_id, "connector_status")
        return [health.model_dump(mode="json") for health in connector_health(database)]

    return server


def main() -> None:
    """Run Alfred's local-only stdio MCP server."""
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
