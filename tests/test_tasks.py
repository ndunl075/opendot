from datetime import UTC, datetime
from pathlib import Path

import pytest

from alfred.db import Database
from alfred.events import EventStore
from alfred.tasks import TaskError, TaskStore


def _source_event(database: Database, external_id: str = "one") -> str:
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection,
                source="test",
                external_id=external_id,
                occurred_at=datetime(2026, 8, 11, tzinfo=UTC),
                content="raw",
                metadata={},
            )
    return event.id


def test_create_rejects_an_empty_title(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    event_id = _source_event(database)

    with database.connect() as connection:
        with database.transaction(connection):
            with pytest.raises(TaskError, match="cannot be empty"):
                TaskStore.create(connection, title="   ", source_event_id=event_id)


def test_upsert_creates_a_task_when_no_id_is_given(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    event_id = _source_event(database)

    with database.connect() as connection:
        with database.transaction(connection):
            task = TaskStore.upsert(connection, title="Submit paper", source_event_id=event_id)

    assert task.state == "open"
    assert task.title == "Submit paper"


def test_upsert_without_a_task_id_or_source_event_raises(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()

    with database.connect() as connection:
        with database.transaction(connection):
            with pytest.raises(TaskError, match="source_event_id is required"):
                TaskStore.upsert(connection, title="Submit paper")


def test_upsert_updates_title_and_due_date_of_an_existing_task(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    event_id = _source_event(database)
    with database.connect() as connection:
        with database.transaction(connection):
            created = TaskStore.create(connection, title="Draft outline", source_event_id=event_id)

    new_due = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
    with database.connect() as connection:
        with database.transaction(connection):
            updated = TaskStore.upsert(connection, task_id=created.id, title="Draft outline v2", due_at=new_due)

    assert updated.id == created.id
    assert updated.title == "Draft outline v2"
    assert updated.due_at == new_due
    assert updated.state == "open"


def test_upsert_omitting_due_at_on_update_leaves_it_unchanged(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    event_id = _source_event(database)
    due_at = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
    with database.connect() as connection:
        with database.transaction(connection):
            created = TaskStore.create(connection, title="Draft outline", source_event_id=event_id, due_at=due_at)

    with database.connect() as connection:
        with database.transaction(connection):
            # due_at is not passed at all -- must not be silently cleared.
            updated = TaskStore.upsert(connection, task_id=created.id, title="Draft outline v2")

    assert updated.due_at == due_at


def test_upsert_can_explicitly_clear_due_at_on_update(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    event_id = _source_event(database)
    due_at = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
    with database.connect() as connection:
        with database.transaction(connection):
            created = TaskStore.create(connection, title="Draft outline", source_event_id=event_id, due_at=due_at)

    with database.connect() as connection:
        with database.transaction(connection):
            updated = TaskStore.upsert(connection, task_id=created.id, title="Draft outline", due_at=None)

    assert updated.due_at is None


def test_upsert_rejects_an_unknown_task_id(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()

    with database.connect() as connection:
        with database.transaction(connection):
            with pytest.raises(TaskError, match="does not exist"):
                TaskStore.upsert(connection, task_id="missing", title="anything")


def test_complete_marks_an_open_task_completed_and_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    event_id = _source_event(database)
    with database.connect() as connection:
        with database.transaction(connection):
            created = TaskStore.create(connection, title="Submit paper", source_event_id=event_id)

    with database.connect() as connection:
        with database.transaction(connection):
            first = TaskStore.complete(connection, created.id)
    with database.connect() as connection:
        with database.transaction(connection):
            second = TaskStore.complete(connection, created.id)

    assert first.state == "completed"
    assert second.state == "completed"


def test_complete_rejects_an_unknown_or_cancelled_task(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()

    with database.connect() as connection:
        with database.transaction(connection):
            with pytest.raises(TaskError, match="does not exist"):
                TaskStore.complete(connection, "missing")

    event_id = _source_event(database, "two")
    with database.connect() as connection:
        with database.transaction(connection):
            created = TaskStore.create(connection, title="Cancelled task", source_event_id=event_id)
            connection.execute("UPDATE tasks SET state = 'cancelled' WHERE id = ?", (created.id,))

    with database.connect() as connection:
        with database.transaction(connection):
            with pytest.raises(TaskError, match="cannot complete a cancelled task"):
                TaskStore.complete(connection, created.id)
