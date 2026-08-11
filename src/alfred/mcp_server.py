"""Narrow stdio MCP surface for the walking skeleton."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .briefing import BriefingService
from .config import Settings
from .db import Database
from .memory_graph import MemoryGraph


def create_server(database_path: Path | str | None = None) -> FastMCP:
    """Create a read-only server; actions arrive only after policy work exists."""
    settings = Settings.from_environment(Path(database_path) if database_path else None)
    database = Database(settings.database_path)
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
        return MemoryGraph(database).search(query).model_dump(mode="json")

    @server.tool()
    def profile_get() -> dict:
        """Return the local owner node and current, evidence-backed profile relationships."""
        owner, relationships = MemoryGraph(database).profile()
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
