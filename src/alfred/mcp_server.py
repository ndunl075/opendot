"""Narrow stdio MCP surface for the walking skeleton."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import Settings
from .db import Database


def create_server(database_path: Path | str | None = None) -> FastMCP:
    """Create a read-only server; actions arrive only after policy work exists."""
    settings = Settings.from_environment(Path(database_path) if database_path else None)
    database = Database(settings.database_path)
    server = FastMCP("Alfred")

    @server.tool()
    def system_status() -> dict[str, int | str]:
        """Return Alfred's non-sensitive local health and schema status."""
        return database.status()

    return server


def main() -> None:
    """Run Alfred's local-only stdio MCP server."""
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
