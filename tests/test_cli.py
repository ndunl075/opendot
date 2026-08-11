import json
from pathlib import Path

from alfred.cli import main


def test_cli_initializes_audits_and_verifies(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "alfred.db"

    assert main(["--db", str(database_path), "init"]) == 0
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["schema_version"] == 6

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


def test_cli_handles_a_paired_telegram_task(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "alfred.db"
    update_file = tmp_path / "telegram-update.json"
    update_file.write_text(
        json.dumps(
        {
            "update_id": 99,
            "message": {
                "message_id": 1,
                "date": 1_786_198_400,
                "chat": {"id": 20},
                "from": {"id": 10},
                "text": "/task read architecture",
            },
        }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--db",
                str(database_path),
                "telegram-handle",
                "--chat-id",
                "20",
                "--user-id",
                "10",
                "--update-file",
                str(update_file),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["text"] == "Saved task: read architecture"


def test_cli_creates_and_searches_local_graph_records(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "alfred.db"

    assert main(["--db", str(database_path), "memory-self", "--label", "Nico"]) == 0
    owner_id = json.loads(capsys.readouterr().out)["id"]
    assert main(["--db", str(database_path), "memory-entity", "--type", "project", "--label", "Alfred"]) == 0
    project_id = json.loads(capsys.readouterr().out)["id"]
    assert (
        main(
            [
                "--db",
                str(database_path),
                "memory-relation",
                "--source-id",
                owner_id,
                "--predicate",
                "works_on",
                "--target-id",
                project_id,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["predicate"] == "works_on"
    assert main(["--db", str(database_path), "memory-search", "Alfred"]) == 0
    assert json.loads(capsys.readouterr().out)["entities"][0]["id"] == project_id
