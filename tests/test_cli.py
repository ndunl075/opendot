import json
from pathlib import Path

from alfred.cli import main
from alfred.db import Database


def test_cli_initializes_audits_and_verifies(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "alfred.db"

    assert main(["--db", str(database_path), "init"]) == 0
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["schema_version"] == 12

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


def test_cli_reports_connector_status_without_any_configured_connector(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "alfred.db"

    assert main(["--db", str(database_path), "connector-status"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_imports_a_vault_note_as_a_confirmed_memory(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "alfred.db"
    vault = tmp_path / "vault"
    note = vault / "Decisions" / "local-first.md"
    note.parent.mkdir(parents=True)
    note.write_text("Alfred stays local-first.\n", encoding="utf-8")

    assert main(["--db", str(database_path), "vault-import", "--vault", str(vault)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["scanned"], result["imported"]) == (1, 1)

    assert main(["--db", str(database_path), "memory-search", "local-first"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert [memory["statement"] for memory in found["memories"]] == ["Alfred stays local-first."]


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

    assert main(["--db", str(database_path), "memory-alias", "--entity-id", project_id, "AlfredCore"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "entity_id": project_id,
        "alias": "AlfredCore",
        "source": "user:cli",
        "confidence": 1.0,
    }
    assert main(["--db", str(database_path), "memory-search", "AlfredCore"]) == 0
    assert json.loads(capsys.readouterr().out)["entities"][0]["id"] == project_id


def test_cli_corrects_and_forgets_a_local_memory(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "alfred.db"

    assert main(["--db", str(database_path), "remember", "Preferred brief time is 7 AM."]) == 0
    memory_id = json.loads(capsys.readouterr().out)["id"]

    assert main(["--db", str(database_path), "memory-correct", "--memory-id", memory_id, "Preferred brief time is 8 AM."]) == 0
    corrected = json.loads(capsys.readouterr().out)
    assert corrected["supersedes_memory_id"] == memory_id
    assert corrected["statement"] == "Preferred brief time is 8 AM."

    assert (
        main(
            [
                "--db",
                str(database_path),
                "memory-forget-propose",
                "--memory-id",
                corrected["id"],
                "--reason",
                "test cleanup",
                "--actor",
                "nico",
            ]
        )
        == 0
    )
    proposal = json.loads(capsys.readouterr().out)
    assert proposal["action_type"] == "memory_forget"
    assert proposal["preview"] == {"memory_id": corrected["id"], "reason": "test cleanup"}

    assert main(["--db", str(database_path), "approval-approve", "--approval-id", proposal["id"], "--actor", "nico"]) == 0
    issued = json.loads(capsys.readouterr().out)

    assert (
        main(
            [
                "--db",
                str(database_path),
                "memory-forget-execute",
                "--approval-id",
                proposal["id"],
                "--actor",
                "nico",
                "--token",
                issued["token"],
            ]
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt == {"memory_id": corrected["id"], "idempotency_key": f"memory_forget:{proposal['id']}", "replayed": False}

    assert main(["--db", str(database_path), "memory-search", "brief time"]) == 0
    remaining_ids = [item["id"] for item in json.loads(capsys.readouterr().out)["memories"]]
    assert corrected["id"] not in remaining_ids

    assert main(["--db", str(database_path), "memory-search", "brief time"]) == 0
    # The forgotten correction and superseded original remain inspectable in
    # history, but neither is active recall.
    remaining_ids = [item["id"] for item in json.loads(capsys.readouterr().out)["memories"]]
    assert corrected["id"] not in remaining_ids
    assert remaining_ids == []


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


def test_cli_creates_updates_and_completes_a_task(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "alfred.db"

    assert main(["--db", str(database_path), "task-upsert", "Submit paper", "--due-at", "2026-08-20T09:00:00-04:00"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert (created["title"], created["state"], created["due_at"]) == ("Submit paper", "open", "2026-08-20T09:00:00-04:00")

    assert (
        main(
            [
                "--db",
                str(database_path),
                "task-upsert",
                "Submit final paper",
                "--task-id",
                created["id"],
                "--due-at",
                "2026-08-21T09:00:00-04:00",
            ]
        )
        == 0
    )
    updated = json.loads(capsys.readouterr().out)
    assert updated["id"] == created["id"]
    assert updated["title"] == "Submit final paper"

    assert main(["--db", str(database_path), "task-complete", "--task-id", created["id"]]) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["state"] == "completed"


def test_cli_sets_a_reminder_creating_its_own_task(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "alfred.db"

    assert (
        main(
            [
                "--db",
                str(database_path),
                "reminder-set",
                "Call advisor",
                "--run-at",
                "2026-08-15T09:00:00-04:00",
                "--chat-id",
                "20",
            ]
        )
        == 0
    )
    job = json.loads(capsys.readouterr().out)
    assert job["run_at"] == "2026-08-15T13:00:00Z"
    with Database(database_path).connect() as connection:
        row = connection.execute("SELECT title, state FROM tasks WHERE id = ?", (job["task_id"],)).fetchone()
    assert (row["title"], row["state"]) == ("Call advisor", "open")


def test_cli_proposes_a_gmail_draft_without_any_google_credential(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "alfred.db"

    assert (
        main(
            [
                "--db",
                str(database_path),
                "gmail-draft-propose",
                "--actor",
                "nico",
                "--to",
                "advisor@school.example",
                "--subject",
                "Question",
                "Quick question about the deadline.",
            ]
        )
        == 0
    )
    proposed = json.loads(capsys.readouterr().out)
    assert proposed["action_type"] == "gmail_draft_create"
    assert proposed["preview"] == {
        "to": "advisor@school.example",
        "subject": "Question",
        "body": "Quick question about the deadline.",
    }
    assert proposed["state"] == "pending"


def test_cli_service_configure_stores_the_run_args_for_the_windows_service(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    # service-configure always writes to ./.alfred (matching scripts/install.ps1
    # and README's fixed local-data convention), so redirect CWD rather than
    # using --db, which only controls the database path.
    monkeypatch.chdir(tmp_path)

    assert main(["service-configure", "run", "--pair", "123:456", "--chat-id", "123"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["args"] == ["run", "--pair", "123:456", "--chat-id", "123"]
    assert Path(result["config_path"]).resolve() == (tmp_path / ".alfred" / "service.json").resolve()
    from alfred.winservice import load_configured_args

    assert load_configured_args(alfred_dir=tmp_path / ".alfred") == ["run", "--pair", "123:456", "--chat-id", "123"]
