from datetime import UTC, datetime, timedelta
from pathlib import Path

from alfred.connector_health import connector_health
from alfred.db import Database


def _insert_sync_state(
    database: Database,
    *,
    connector: str,
    account: str,
    last_success_at: str | None,
    last_error: str | None,
) -> None:
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                "INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at) "
                "VALUES (?, ?, NULL, ?, ?, ?)",
                (connector, account, last_success_at, last_error, datetime.now(UTC).isoformat()),
            )


def test_never_synced_when_there_is_no_recorded_success(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _insert_sync_state(database, connector="github", account="self", last_success_at=None, last_error=None)

    results = connector_health(database)

    assert [item.model_dump() for item in results] == [
        {
            "connector": "github",
            "account": "self",
            "state": "never_synced",
            "last_success_at": None,
            "last_error": None,
        }
    ]


def test_ok_when_the_last_success_is_recent(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    _insert_sync_state(
        database, connector="gmail", account="self", last_success_at=(now - timedelta(hours=1)).isoformat(), last_error=None
    )

    results = connector_health(database, now=now)

    assert results[0].state == "ok"


def test_stale_when_the_last_success_exceeds_the_threshold(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    _insert_sync_state(
        database,
        connector="google_calendar",
        account="primary",
        last_success_at=(now - timedelta(hours=25)).isoformat(),
        last_error=None,
    )

    results = connector_health(database, now=now, stale_after=timedelta(hours=24))

    assert results[0].state == "stale"


def test_error_takes_priority_even_with_a_recent_success(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
    _insert_sync_state(
        database,
        connector="canvas",
        account="self",
        last_success_at=(now - timedelta(minutes=5)).isoformat(),
        last_error="ConnectTimeout",
    )

    results = connector_health(database, now=now)

    assert results[0].state == "error"
    assert results[0].last_error == "ConnectTimeout"
