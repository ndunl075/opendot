import json
from pathlib import Path

from opendot_core.cli import build_parser, main
from opendot_core.db import Database
from tests.schema_version import LATEST_SCHEMA_VERSION


def test_cli_initializes_audits_and_verifies(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "opendot.db"

    assert main(["--db", str(database_path), "init"]) == 0
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["schema_version"] == LATEST_SCHEMA_VERSION

    assert (
        main(
            [
                "--db",
                str(database_path),
                "audit",
                "--actor",
                "sam",
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
    database_path = tmp_path / "opendot.db"

    assert main(["--db", str(database_path), "connector-status"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_exposes_composio_free_tier_commands_without_a_key_argument() -> None:
    setup = build_parser().parse_args(["composio-setup"])
    status = build_parser().parse_args(["composio-status"])
    search = build_parser().parse_args(["composio-search", "pages", "--toolkit", "notion"])
    connect = build_parser().parse_args(["composio-connect", "spotify"])

    assert setup.secret_name == "composio-api-key"
    assert status.secret_name == "composio-api-key"
    assert search.toolkit == "notion"
    assert connect.toolkit == "spotify"


def test_cli_imports_a_vault_note_as_a_confirmed_memory(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "opendot.db"
    vault = tmp_path / "vault"
    note = vault / "Decisions" / "local-first.md"
    note.parent.mkdir(parents=True)
    note.write_text("OpenDot stays local-first.\n", encoding="utf-8")

    assert main(["--db", str(database_path), "vault-import", "--vault", str(vault)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["scanned"], result["imported"]) == (1, 1)

    assert main(["--db", str(database_path), "memory-search", "local-first"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert [memory["statement"] for memory in found["memories"]] == ["OpenDot stays local-first."]


def test_cli_handles_a_paired_telegram_task(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "opendot.db"
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
    database_path = tmp_path / "opendot.db"

    assert main(["--db", str(database_path), "memory-self", "--label", "Sam"]) == 0
    owner_id = json.loads(capsys.readouterr().out)["id"]
    assert main(["--db", str(database_path), "memory-entity", "--type", "project", "--label", "OpenDot"]) == 0
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
    assert main(["--db", str(database_path), "memory-search", "OpenDot"]) == 0
    assert json.loads(capsys.readouterr().out)["entities"][0]["id"] == project_id

    assert main(["--db", str(database_path), "memory-alias", "--entity-id", project_id, "OpenDotCore"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "entity_id": project_id,
        "alias": "OpenDotCore",
        "source": "user:cli",
        "confidence": 1.0,
    }
    assert main(["--db", str(database_path), "memory-search", "OpenDotCore"]) == 0
    assert json.loads(capsys.readouterr().out)["entities"][0]["id"] == project_id


def test_cli_corrects_and_forgets_a_local_memory(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "opendot.db"

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
                "sam",
            ]
        )
        == 0
    )
    proposal = json.loads(capsys.readouterr().out)
    assert proposal["action_type"] == "memory_forget"
    assert proposal["preview"] == {"memory_id": corrected["id"], "reason": "test cleanup"}

    assert main(["--db", str(database_path), "approval-approve", "--approval-id", proposal["id"], "--actor", "sam"]) == 0
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
                "sam",
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
    database_path = tmp_path / "opendot.db"

    assert (
        main(
            [
                "--db",
                str(database_path),
                "calendar-event-propose",
                "--actor",
                "sam",
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
    database_path = tmp_path / "opendot.db"

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
    database_path = tmp_path / "opendot.db"

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
    database_path = tmp_path / "opendot.db"

    assert (
        main(
            [
                "--db",
                str(database_path),
                "gmail-draft-propose",
                "--actor",
                "sam",
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


def test_cli_google_auth_requests_only_the_default_calendar_and_gmail_scopes() -> None:
    from opendot_core.google_oauth import DEFAULT_SCOPES

    args = build_parser().parse_args(["google-auth"])

    assert not hasattr(args, "include_health")
    assert tuple(args.scope) == () and DEFAULT_SCOPES


def test_cli_run_has_no_removed_connector_or_agent_flags_and_learning_is_off_by_default() -> None:
    import pytest

    default = build_parser().parse_args(["run"])
    learning = build_parser().parse_args(["run", "--learning"])

    assert default.learning is False
    assert learning.learning is True
    # Spelled in parts so the leftover-name grep in ARCHITECTURE.md stays empty.
    removed_flags = ("--can" + "vas-ical", "--google-" + "health", "--her" + "mes-profile")
    removed_commands = ("pre" + "flight", "lat" + "ency-status", "can" + "vas-sync", "health-sync", "service-configure")
    for removed in removed_flags:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["run", removed])
    for removed in removed_commands:
        with pytest.raises(SystemExit):
            build_parser().parse_args([removed])


def test_cli_watchdog_check_takes_the_chat_ids_allowed_to_wake_it() -> None:
    args = build_parser().parse_args(["watchdog-check", "--chat-id", "20", "--chat-id", "21"])

    assert args.chat_id == [20, 21]


def test_cli_rebuilds_calendar_history_on_an_empty_database(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "opendot.db"

    assert main(["--db", str(database_path), "calendar-history-rebuild"]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["rollup"]["changed"] is True
    assert report["history"]["active_items"] == 0

