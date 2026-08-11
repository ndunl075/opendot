"""Narrow stdio MCP surface for the walking skeleton."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .briefing import BriefingService
from .config import Settings
from .db import Database
from .memory_graph import MemoryGraph
from .policy import PolicyStore


def create_server(database_path: Path | str | None = None, *, client_id: str = "local-mcp") -> FastMCP:
    """Create a read-only server; actions arrive only after policy work exists."""
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

    return server


def main() -> None:
    """Run Alfred's local-only stdio MCP server."""
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
