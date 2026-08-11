import json
from pathlib import Path

from alfred.cli import main


def test_cli_initializes_audits_and_verifies(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "alfred.db"

    assert main(["--db", str(database_path), "init"]) == 0
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["schema_version"] == 1

    assert (
        main(
            [
                "--db",
                str(database_path),
                "audit",
                "--actor",
                "nico",
                "--tool",
                "system_status",
                "--outcome",
                "ok",
            ]
        )
        == 0
    )
    assert "audit_record_id" in json.loads(capsys.readouterr().out)

    assert main(["--db", str(database_path), "audit-verify"]) == 0
    assert json.loads(capsys.readouterr().out) == {"valid": True}
