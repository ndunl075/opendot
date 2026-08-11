from pathlib import Path

import pytest

from alfred.audit import AuditLog
from alfred.db import Database
from alfred.documents import DocumentStore
from alfred.memory_graph import MemoryGraph
from alfred.vault import VaultError, VaultImporter, VaultProjector


def test_projected_entity_is_plain_markdown_with_stable_id(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    entity = MemoryGraph(database).create_entity(entity_type="project", label="Alfred", properties={"stage": "MVP"})
    vault = tmp_path / "vault"

    projection = VaultProjector(database, vault).project_entity(entity.id)

    assert projection.conflict_copy is False
    contents = projection.path.read_text(encoding="utf-8")
    assert f"alfred_id: {entity.id}" in contents
    assert "managed: true" in contents
    assert "# Alfred" in contents
    assert "\"stage\": \"MVP\"" in contents
    assert AuditLog(database).verify() is True


def test_sensitive_graph_records_are_not_exported(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    entity = MemoryGraph(database).create_entity(entity_type="person", label="Private", sensitivity="sensitive")

    with pytest.raises(VaultError, match="not exported"):
        VaultProjector(database, tmp_path / "vault").project_entity(entity.id)


def test_manual_file_is_preserved_with_a_conflict_copy(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    entity = MemoryGraph(database).create_entity(entity_type="project", label="Alfred")
    vault = tmp_path / "vault"
    path = vault / "Generated" / "Entities" / f"{entity.id}.md"
    path.parent.mkdir(parents=True)
    path.write_text("# My manual note\n", encoding="utf-8")

    projection = VaultProjector(database, vault).project_entity(entity.id)

    assert projection.conflict_copy is True
    assert path.read_text(encoding="utf-8") == "# My manual note\n"
    assert projection.path.name.startswith(f"{entity.id}.alfred-conflict-")
    assert "managed: true" in projection.path.read_text(encoding="utf-8")


def test_only_confirmed_memories_are_projected(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    candidate = graph.remember("Possibly likes tea.", status="candidate", confidence=0.3, confirmed=False)
    confirmed = graph.remember("Likes coffee.")
    projector = VaultProjector(database, tmp_path / "vault")

    with pytest.raises(VaultError, match="only confirmed"):
        projector.project_memory(candidate.id)
    projection = projector.project_memory(confirmed.id)

    assert "Likes coffee." in projection.path.read_text(encoding="utf-8")


def _write_note(vault: Path, relative_path: str, contents: str) -> Path:
    path = vault / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


def test_new_note_becomes_a_confirmed_evidence_backed_memory(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    vault = tmp_path / "vault"
    _write_note(vault, "People/advisor.md", "My advisor is Dr. Rivera.\n")

    result = VaultImporter(database, vault).sync()

    assert (result.scanned, result.imported, result.updated, result.skipped) == (1, 1, 0, 0)
    graph = MemoryGraph(database)
    found = graph.search("advisor Rivera")
    assert len(found.memories) == 1
    memory = found.memories[0]
    assert memory.statement == "My advisor is Dr. Rivera."
    assert memory.status == "confirmed"
    evidence = graph.evidence_for("memory", memory.id)
    assert len(evidence) == 1
    with database.connect() as connection:
        event_row = connection.execute("SELECT id FROM events WHERE source = 'obsidian_vault'").fetchone()
        documents = DocumentStore.for_event(connection, event_row["id"])
    assert len(documents) == 1
    assert documents[0].uri.endswith("advisor.md")
    assert documents[0].mime_type == "text/markdown"
    assert documents[0].checksum
    assert AuditLog(database).verify() is True


def test_reimporting_an_unchanged_note_does_not_create_a_second_memory(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    vault = tmp_path / "vault"
    _write_note(vault, "Decisions/local-first.md", "Alfred stays local-first.\n")
    importer = VaultImporter(database, vault)

    first = importer.sync()
    second = importer.sync()

    assert (first.imported, first.updated) == (1, 0)
    assert (second.imported, second.updated, second.scanned) == (0, 0, 1)
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_editing_a_note_supersedes_its_memory_instead_of_duplicating(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    vault = tmp_path / "vault"
    path = _write_note(vault, "Projects/alfred.md", "Alfred targets Windows first.\n")
    importer = VaultImporter(database, vault)
    first = importer.sync()

    path.write_text("Alfred targets Windows and Linux.\n", encoding="utf-8")
    second = importer.sync()

    assert (first.imported, second.updated) == (1, 1)
    graph = MemoryGraph(database)
    with database.connect() as connection:
        statuses = {
            row["statement"]: row["status"]
            for row in connection.execute("SELECT statement, status FROM memories")
        }
    assert statuses == {
        "Alfred targets Windows first.": "superseded",
        "Alfred targets Windows and Linux.": "confirmed",
    }
    # The superseded original stays visible as history, per Alfred's usual correction rule.
    confirmed = [memory for memory in graph.search("Alfred targets").memories if memory.status == "confirmed"]
    assert [memory.statement for memory in confirmed] == ["Alfred targets Windows and Linux."]


def test_deleting_a_note_does_not_forget_its_memory(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    vault = tmp_path / "vault"
    path = _write_note(vault, "Inbox/note.md", "A note that will be deleted from disk.\n")
    importer = VaultImporter(database, vault)
    importer.sync()
    path.unlink()

    result = importer.sync()

    assert result.scanned == 0
    graph = MemoryGraph(database)
    assert len(graph.search("note deleted disk").memories) == 1


def test_generated_notes_are_never_reimported_as_testimony(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    vault = tmp_path / "vault"
    entity = MemoryGraph(database).create_entity(entity_type="project", label="Alfred")
    VaultProjector(database, vault).project_entity(entity.id)

    result = VaultImporter(database, vault).sync()

    assert result.scanned == 1
    assert result.skipped == 1
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_frontmatter_sensitivity_is_honored_and_invalid_values_fall_back(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    vault = tmp_path / "vault"
    _write_note(vault, "People/private.md", "---\nsensitivity: sensitive\n---\n\nA private note about someone.\n")
    _write_note(vault, "People/normal.md", "---\nsensitivity: not-a-real-level\n---\n\nAn ordinary note.\n")

    VaultImporter(database, vault).sync()

    with database.connect() as connection:
        levels = {
            row["statement"]: row["sensitivity"] for row in connection.execute("SELECT statement, sensitivity FROM memories")
        }
    assert levels == {
        "A private note about someone.": "sensitive",
        "An ordinary note.": "personal",
    }


def test_a_forgotten_memory_is_freshly_reimported_on_the_next_edit(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    vault = tmp_path / "vault"
    path = _write_note(vault, "Courses/notes.md", "First version of the note.\n")
    graph = MemoryGraph(database)
    importer = VaultImporter(database, vault)
    importer.sync()
    memories = graph.search("First version note").memories
    graph.forget_memory(memories[0].id)

    path.write_text("Second version of the note.\n", encoding="utf-8")
    result = importer.sync()

    assert result.imported == 1
    assert result.updated == 0
    assert len(graph.search("Second version note").memories) == 1
