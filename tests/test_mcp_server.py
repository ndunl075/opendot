from pathlib import Path

from alfred.mcp_server import create_server


def test_mcp_server_can_be_constructed(tmp_path: Path) -> None:
    server = create_server(tmp_path / "alfred.db")

    assert server.name == "Alfred"
