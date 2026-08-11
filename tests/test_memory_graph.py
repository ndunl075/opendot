from datetime import UTC, datetime
from pathlib import Path

import pytest

from alfred.audit import AuditLog
from alfred.db import Database
from alfred.memory_graph import GraphError, MemoryGraph


def test_self_identity_is_permanent_and_singleton(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)

    first = graph.ensure_self("Nico")
    second = graph.ensure_self("Different label")

    assert first.id == second.id
    assert second.label == "Nico"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM entities WHERE entity_type = 'self'").fetchone()[0] == 1


def test_only_confirmed_registry_types_can_be_used(tmp_path: Path) -> None:
    graph = MemoryGraph(Database(tmp_path / "alfred.db"))

    with pytest.raises(GraphError, match="entity type is not confirmed"):
        graph.create_entity(entity_type="mystery", label="Unknown")


def test_single_state_relationship_closes_the_previous_current_state(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    owner = graph.ensure_self("Nico")
    first_school = graph.create_entity(entity_type="school", label="First University")
    second_school = graph.create_entity(entity_type="school", label="Second University")

    first = graph.add_relationship(
        source_entity_id=owner.id,
        predicate="studies_at",
        target_entity_id=first_school.id,
        valid_from=datetime(2025, 8, 1, tzinfo=UTC),
    )
    second = graph.add_relationship(
        source_entity_id=owner.id,
        predicate="studies_at",
        target_entity_id=second_school.id,
        valid_from=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert first.valid_to is None
    assert second.valid_to is None
    with database.connect() as connection:
        rows = connection.execute("SELECT target_entity_id, valid_to FROM relationships ORDER BY valid_from").fetchall()
    assert rows[0]["target_entity_id"] == first_school.id
    assert rows[0]["valid_to"] == "2026-08-01T00:00:00+00:00"
    assert rows[1]["target_entity_id"] == second_school.id
    assert rows[1]["valid_to"] is None


def test_fts_search_returns_memory_and_one_hop_relationship_context(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    owner = graph.ensure_self("Nico")
    project = graph.create_entity(entity_type="project", label="Alfred Capstone")
    graph.add_relationship(source_entity_id=owner.id, predicate="works_on", target_entity_id=project.id)
    memory = graph.remember("Nico wants Alfred Capstone to stay local first.")

    result = graph.search("Alfred Capstone")

    assert [entity.id for entity in result.entities] == [project.id]
    assert [item.id for item in result.memories] == [memory.id]
    assert [relationship.predicate for relationship in result.relationships] == ["works_on"]
    assert AuditLog(database).verify() is True


def test_forget_tombstones_a_memory_and_removes_it_from_search(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    memory = graph.remember("My locker combination is 12-34-56.")

    forgotten = graph.forget_memory(memory.id, reason="no longer relevant")

    assert forgotten.status == "deleted"
    assert graph.search("locker combination").memories == []
    with database.connect() as connection:
        row = connection.execute("SELECT valid_to FROM memories WHERE id = ?", (memory.id,)).fetchone()
        history = connection.execute(
            "SELECT previous_status, next_status, reason FROM memory_history WHERE memory_id = ?", (memory.id,)
        ).fetchone()
        fts_hit = connection.execute("SELECT COUNT(*) FROM memory_fts WHERE memory_id = ?", (memory.id,)).fetchone()[0]
    assert row["valid_to"] is not None
    assert history["previous_status"] == "confirmed"
    assert history["next_status"] == "deleted"
    assert history["reason"] == "no longer relevant"
    assert fts_hit == 0
    assert AuditLog(database).verify() is True


def test_forget_rejects_an_already_deleted_memory(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    memory = graph.remember("Temporary note.")
    graph.forget_memory(memory.id)

    with pytest.raises(GraphError, match="already deleted"):
        graph.forget_memory(memory.id)


def test_correction_supersedes_memory_without_erasing_history(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    original = graph.remember("My preferred briefing time is 7 AM.")

    replacement = graph.supersede_memory(original.id, "My preferred briefing time is 8 AM.")

    assert replacement.supersedes_memory_id == original.id
    with database.connect() as connection:
        old = connection.execute("SELECT status, valid_to FROM memories WHERE id = ?", (original.id,)).fetchone()
        history = connection.execute("SELECT next_status FROM memory_history WHERE memory_id = ?", (original.id,)).fetchone()
    assert old["status"] == "superseded"
    assert old["valid_to"] is not None
    assert history["next_status"] == "superseded"
