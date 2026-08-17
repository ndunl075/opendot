import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alfred.audit import AuditLog
from alfred.db import Database
from alfred.documents import DocumentStore
from alfred.events import EventStore
from alfred.memory_graph import MemoryGraph
from alfred.vault import VaultError, VaultImporter, VaultProjector, _fs


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


def test_time_range_export_covers_only_memories_recorded_in_the_window(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    old = graph.remember("Recorded well before the window.")
    inside = graph.remember("Recorded inside the window.")
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                "UPDATE memories SET created_at = ? WHERE id = ?",
                ("2026-01-05T00:00:00+00:00", old.id),
            )
            connection.execute(
                "UPDATE memories SET created_at = ? WHERE id = ?",
                ("2026-03-10T00:00:00+00:00", inside.id),
            )

    result = VaultProjector(database, tmp_path / "vault").export_by_time_range(
        since=datetime(2026, 3, 1, tzinfo=UTC), until=datetime(2026, 4, 1, tzinfo=UTC)
    )

    assert result.selector == "time_range"
    assert [p.path.stem for p in result.projections] == [inside.id]


def test_time_range_bounds_are_half_open_so_adjacent_windows_do_not_overlap(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    boundary = MemoryGraph(database).remember("Recorded exactly on the boundary.")
    with database.connect() as connection:
        with database.transaction(connection):
            connection.execute(
                "UPDATE memories SET created_at = ? WHERE id = ?",
                ("2026-04-01T00:00:00+00:00", boundary.id),
            )
    projector = VaultProjector(database, tmp_path / "vault")

    march = projector.export_by_time_range(
        since=datetime(2026, 3, 1, tzinfo=UTC), until=datetime(2026, 4, 1, tzinfo=UTC)
    )
    april = projector.export_by_time_range(
        since=datetime(2026, 4, 1, tzinfo=UTC), until=datetime(2026, 5, 1, tzinfo=UTC)
    )

    # `until` is exclusive and `since` inclusive, so it lands in exactly one.
    assert march.projections == []
    assert [p.path.stem for p in april.projections] == [boundary.id]


def test_bulk_selectors_never_export_a_memory_a_single_export_would_refuse(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    safe = graph.remember("An ordinary personal note about rowing.")
    secret = graph.remember("A rowing password nobody should export.", sensitivity="secret")
    candidate = graph.remember(
        "Maybe something about rowing.", status="candidate", confirmed=False, confidence=0.3
    )

    result = VaultProjector(database, tmp_path / "vault").export_by_topic("rowing")

    assert [p.path.stem for p in result.projections] == [safe.id]
    assert secret.id not in [p.path.stem for p in result.projections]
    assert candidate.id not in [p.path.stem for p in result.projections]


def test_topic_export_receipt_does_not_retain_the_query_text(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    MemoryGraph(database).remember("A note mentioning chemotherapy scheduling.")

    result = VaultProjector(database, tmp_path / "vault").export_by_topic("chemotherapy")

    # The search phrasing can itself be sensitive; the receipt records how the
    # set was chosen, never what was typed to choose it.
    assert "chemotherapy" not in result.model_dump_json()
    assert result.selector == "topic"


def test_a_person_note_creates_a_confirmed_entity_that_links_then_resolve(tmp_path: Path) -> None:
    """Naming someone yourself is an explicit statement, so it is confirmed --
    unlike the unconfirmed people derived from calendar data."""
    database = Database(tmp_path / "alfred.db")
    vault = tmp_path / "vault" / "People"
    vault.mkdir(parents=True)
    (vault / "Alex Chen.md").write_text(
        "---\ntype: person\n---\n\nRowing coach. Met through the club.\n", encoding="utf-8"
    )
    (tmp_path / "vault" / "today.md").write_text("Practice with [[Alex Chen]].\n", encoding="utf-8")

    result = VaultImporter(database, tmp_path / "vault").sync()

    entity = MemoryGraph(database).resolve_entity_by_name("Alex Chen")
    assert entity is not None
    assert entity.entity_type == "person"
    assert entity.confirmed is True
    # The note declaring the person is imported before the note linking to it,
    # so the link resolves in the same pass.
    assert result.linked == 1


def test_a_note_declaring_an_unknown_type_creates_nothing(tmp_path: Path) -> None:
    """Only registry types; a typo must not mint a new kind of thing."""
    database = Database(tmp_path / "alfred.db")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Thing.md").write_text("---\ntype: gadget\n---\n\nA gadget.\n", encoding="utf-8")

    VaultImporter(database, vault).sync()

    assert MemoryGraph(database).resolve_entity_by_name("Thing") is None


def test_a_note_cannot_mint_a_second_owner(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Impostor.md").write_text("---\ntype: self\n---\n\nMe, allegedly.\n", encoding="utf-8")

    VaultImporter(database, vault).sync()

    assert MemoryGraph(database).resolve_entity_by_name("Impostor") is None


def test_wiki_link_targets_ignores_display_text_and_headings() -> None:
    from alfred.vault import wiki_link_targets

    body = "Talked to [[Alex Chen|Alex]] about [[Northwind#Roadmap]] and [[Alex Chen]] again."

    # Display text and heading are presentation and location; neither changes
    # which note is referenced. The repeat is emphasis, not new evidence.
    assert wiki_link_targets(body) == ["Alex Chen", "Northwind"]


def test_a_wiki_link_records_provenance_against_the_named_entity(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    entity = graph.create_entity(entity_type="project", label="Northwind")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("Shipped the [[Northwind]] rewrite today.\n", encoding="utf-8")

    result = VaultImporter(database, vault).sync()

    assert result.linked == 1
    assert result.unresolved_links == 0
    evidence = graph.evidence_for("entity", entity.id)
    assert len(evidence) == 1
    assert evidence[0].source_event_id is not None


def test_a_link_to_something_unknown_is_counted_not_invented(tmp_path: Path) -> None:
    """A filename is not evidence that a thing exists."""
    database = Database(tmp_path / "alfred.db")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("Met with [[Someone Nobody Knows]] today.\n", encoding="utf-8")

    result = VaultImporter(database, vault).sync()

    assert result.linked == 0
    assert result.unresolved_links == 1
    assert MemoryGraph(database).resolve_entity_by_name("Someone Nobody Knows") is None


def test_an_ambiguous_name_resolves_to_nothing_rather_than_guessing(tmp_path: Path) -> None:
    """Two possible "Alex" entities beat one wrong merge."""
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    graph.create_entity(entity_type="person", label="Alex")
    graph.create_entity(entity_type="project", label="Alex")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("Spoke to [[Alex]] about it.\n", encoding="utf-8")

    result = VaultImporter(database, vault).sync()

    assert graph.resolve_entity_by_name("Alex") is None
    assert result.linked == 0
    assert result.unresolved_links == 1


def test_an_alias_resolves_a_link_to_its_entity(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    entity = graph.create_entity(entity_type="person", label="Alexander Chen")
    graph.add_alias(entity_id=entity.id, alias="Alex Chen")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("Lunch with [[alex chen]].\n", encoding="utf-8")

    result = VaultImporter(database, vault).sync()

    assert result.linked == 1
    assert len(graph.evidence_for("entity", entity.id)) == 1


def test_links_in_alfreds_own_generated_notes_are_never_imported(tmp_path: Path) -> None:
    """Generated notes are Alfred's writing, not the owner's testimony."""
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    entity = graph.create_entity(entity_type="project", label="Northwind")
    vault = tmp_path / "vault" / "Generated"
    vault.mkdir(parents=True)
    (vault / "gen.md").write_text(
        "---\nmanaged: true\n---\n\nAbout [[Northwind]].\n", encoding="utf-8"
    )

    result = VaultImporter(database, tmp_path / "vault").sync()

    assert result.linked == 0
    assert graph.evidence_for("entity", entity.id) == []


def test_a_wiki_link_never_creates_a_relationship_edge(tmp_path: Path) -> None:
    """What a bare link *means* is a typed, registry-validated decision."""
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    entity = graph.create_entity(entity_type="project", label="Northwind")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("Notes on [[Northwind]].\n", encoding="utf-8")

    VaultImporter(database, vault).sync()

    with database.connect() as connection:
        edges = connection.execute(
            "SELECT COUNT(*) FROM relationships WHERE source_entity_id = ? OR target_entity_id = ?",
            (entity.id, entity.id),
        ).fetchone()[0]
    assert edges == 0


def test_fs_leaves_ordinary_paths_untouched() -> None:
    ordinary = Path("vault") / "Generated" / "Entities" / "note.md"

    # No prefix on a short path, on an already-prefixed one, or off-Windows.
    assert _fs(ordinary) == ordinary
    assert "?" not in str(_fs(ordinary))


@pytest.mark.skipif(os.name != "nt", reason="MAX_PATH is a Windows-only limit")
def test_projection_survives_a_vault_root_deep_enough_to_pass_max_path(tmp_path: Path) -> None:
    """A deep vault root must not turn an export into a bare FileNotFoundError.

    Alfred picks the filename here (a 36-character UUID, plus 33 more for a
    conflict copy), so the limit can be crossed by an ordinary export into a
    vault the operator nested a few levels too deep.
    """
    database = Database(tmp_path / "alfred.db")
    entity = MemoryGraph(database).create_entity(entity_type="project", label="Alfred")
    # Pad the root until the generated conflict filename is comfortably past
    # the limit rather than hovering at it.
    vault = tmp_path / "vault"
    while len(str(vault / "Generated" / "Entities")) + 80 < 300:
        vault = vault / "nested-vault-directory"
    manual = vault / "Generated" / "Entities" / f"{entity.id}.md"
    assert len(str(manual)) > 259
    _fs(manual.parent).mkdir(parents=True)
    _fs(manual).write_text("# My manual note\n", encoding="utf-8")

    projection = VaultProjector(database, vault).project_entity(entity.id)

    # The conflict copy is written, the hand-authored file is untouched, and
    # Projection.path stays plain -- the \\?\ prefix is an I/O detail, not
    # part of the path's identity.
    assert projection.conflict_copy is True
    assert not str(projection.path).startswith("\\\\?\\")
    assert _fs(manual).read_text(encoding="utf-8") == "# My manual note\n"
    assert "managed: true" in _fs(projection.path).read_text(encoding="utf-8")


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


def test_bulk_export_by_source_event_projects_only_confirmed_vault_safe_memories(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection,
                source="test",
                external_id="export-source",
                occurred_at=datetime(2026, 8, 11, tzinfo=UTC),
                content="source",
                metadata={},
            )
    confirmed = graph.remember("Safe exported fact.", source_event_id=event.id)
    candidate = graph.remember("Candidate fact.", source_event_id=event.id, status="candidate", confirmed=False)
    sensitive = graph.remember("Sensitive fact.", source_event_id=event.id, sensitivity="sensitive")

    result = VaultProjector(database, tmp_path / "vault").export_by_source_event(event.id)

    assert [projection.path.name for projection in result.projections] == [f"{confirmed.id}.md"]
    assert result.skipped_memory_ids == [candidate.id, sensitive.id]
    assert "Safe exported fact." in result.projections[0].path.read_text(encoding="utf-8")
    assert AuditLog(database).verify() is True


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


def test_export_by_person_covers_events_they_organized(tmp_path: Path) -> None:
    """The last of section 4's five selectors."""
    from alfred.events import EventStore
    from alfred.people import PeopleService

    database = Database(tmp_path / "alfred.db")
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection,
                source="google_calendar",
                external_id="evt-1",
                occurred_at=datetime.now(UTC),
                content="Practice",
                metadata={"organizer": {"email": "coach@example.com"}},
                sensitivity="personal",
            )
    graph = MemoryGraph(database)
    theirs = graph.remember("Practice moved to Thursday.", source_event_id=event.id)
    graph.remember("Something else entirely.")
    PeopleService(database).sync()
    coach = graph.resolve_entity_by_name("coach@example.com")
    assert coach is not None

    result = VaultProjector(database, tmp_path / "vault").export_by_person(coach.id)

    assert result.selector == "person"
    assert [p.path.stem for p in result.projections] == [theirs.id]


def test_export_by_person_refuses_an_unknown_entity(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")

    with pytest.raises(VaultError, match="does not exist"):
        VaultProjector(database, tmp_path / "vault").export_by_person("nobody")
