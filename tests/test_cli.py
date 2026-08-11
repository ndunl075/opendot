import json
from pathlib import Path

from alfred.cli import main


def test_cli_initializes_audits_and_verifies(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "alfred.db"

    assert main(["--db", str(database_path), "init"]) == 0
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["schema_version"] == 8

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


def test_cli_corrects_and_forgets_a_local_memory(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "alfred.db"

    assert main(["--db", str(database_path), "remember", "Preferred brief time is 7 AM."]) == 0
    memory_id = json.loads(capsys.readouterr().out)["id"]

    assert main(["--db", str(database_path), "memory-correct", "--memory-id", memory_id, "Preferred brief time is 8 AM."]) == 0
    corrected = json.loads(capsys.readouterr().out)
    assert corrected["supersedes_memory_id"] == memory_id
    assert corrected["statement"] == "Preferred brief time is 8 AM."

    assert main(["--db", str(database_path), "forget", "--memory-id", corrected["id"], "--reason", "test cleanup"]) == 0
    forgotten = json.loads(capsys.readouterr().out)
    assert forgotten["status"] == "deleted"

    assert main(["--db", str(database_path), "memory-search", "brief time"]) == 0
    # The forgotten (corrected) statement is gone; the superseded original stays visible as history.
    remaining_ids = [item["id"] for item in json.loads(capsys.readouterr().out)["memories"]]
    assert corrected["id"] not in remaining_ids
    assert remaining_ids == [memory_id]


def test_cli_proposes_a_calendar_event_without_any_google_credential(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "alfred.db"

    assert (
        main(
            [
                "--db",
                str(database_path),
                "calendar-event-propose",
                "--actor",
                "nico",
                "--summary",
                "Advisor meeting",
                "--start",
                "2026-08-15T10:00:00-04:00",
                "--end",
                "2026-08-15T11:00:00-04:00",
            ]
        )
        == 0
    )
    proposed = json.loads(capsys.readouterr().out)
    assert proposed["action_type"] == "calendar_event_create"
    assert proposed["preview"]["summary"] == "Advisor meeting"
    assert proposed["state"] == "pending"
