from datetime import UTC, datetime
from pathlib import Path

from alfred.db import Database
from alfred.documents import DocumentStore
from alfred.events import EventStore


def test_append_links_a_document_to_its_event(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection,
                source="obsidian_vault",
                external_id="Decisions/local-first.md:abc123",
                occurred_at=datetime(2026, 8, 11, tzinfo=UTC),
                content="Alfred stays local-first.",
                metadata={},
            )
            document = DocumentStore.append(
                connection,
                event_id=event.id,
                uri="Decisions/local-first.md",
                checksum="abc123",
                mime_type="text/markdown",
                retention_policy="vault-local",
            )

    assert document.event_id == event.id
    assert document.checksum == "abc123"
    with database.connect() as connection:
        linked = DocumentStore.for_event(connection, event.id)
    assert [item.uri for item in linked] == ["Decisions/local-first.md"]
    assert linked[0].mime_type == "text/markdown"
    assert linked[0].retention_policy == "vault-local"


def test_for_event_returns_nothing_for_an_event_with_no_document(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection,
                source="github",
                external_id="thread-1:2026-08-11T00:00:00Z",
                occurred_at=datetime(2026, 8, 11, tzinfo=UTC),
                content="Fix flaky test",
                metadata={},
            )

    with database.connect() as connection:
        assert DocumentStore.for_event(connection, event.id) == []
