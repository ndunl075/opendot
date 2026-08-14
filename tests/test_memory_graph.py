from datetime import UTC, datetime
from pathlib import Path

import pytest

from alfred.audit import AuditLog
from alfred.db import Database
from alfred.events import EventStore
from alfred.memory_graph import GraphError, MemoryActions, MemoryGraph
from alfred.memory_learning import MemoryFeedbackStore
from alfred.policy import ApprovalService, PolicyError


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


def test_explicit_feedback_reorders_only_the_retrieved_candidate_set(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    first = graph.remember("Calculus exam review on Friday")
    preferred = graph.remember("Calculus exam room and schedule")
    before = [memory.id for memory in graph.search("Calculus exam").memories]
    assert set(before) == {first.id, preferred.id}

    MemoryFeedbackStore(database).record(
        preferred.id, query="Calculus exam", outcome="relevant", actor="user:test"
    )
    MemoryFeedbackStore(database).record(
        first.id, query="Calculus exam", outcome="irrelevant", actor="user:test"
    )

    after = [memory.id for memory in graph.search("Calculus exam").memories]
    assert after == [preferred.id, first.id]


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


class _FakeEmbeddingProvider:
    """Hand-built vectors: 'stressed about deadline' is close to 'anxious about due date'."""

    model_name = "fake-v1"
    _vectors = {
        "I feel anxious about my thesis due date.": [1.0, 0.0],
        "Remember to buy milk.": [0.0, 1.0],
        "stressed about deadline": [0.9, 0.1],
    }

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[text] for text in texts]


def test_hybrid_search_surfaces_a_semantic_match_fts_alone_would_miss(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database, embedding_provider=_FakeEmbeddingProvider())
    relevant = graph.remember("I feel anxious about my thesis due date.")
    graph.remember("Remember to buy milk.")

    keyword_only = MemoryGraph(database).search("stressed about deadline")
    hybrid = graph.search("stressed about deadline")

    assert keyword_only.memories == []
    assert [memory.id for memory in hybrid.memories] == [relevant.id]


def test_forget_removes_the_embedding_alongside_the_fts_row(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database, embedding_provider=_FakeEmbeddingProvider())
    memory = graph.remember("I feel anxious about my thesis due date.")

    graph.forget_memory(memory.id)

    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM embeddings WHERE subject_id = ?", (memory.id,)).fetchone()[0]
    assert count == 0
    assert graph.search("stressed about deadline").memories == []


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


def _append_event(database: Database, external_id: str, content: str) -> str:
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection,
                source="test",
                external_id=external_id,
                occurred_at=datetime(2026, 8, 11, tzinfo=UTC),
                content=content,
                metadata={},
            )
    return event.id


def test_remember_with_a_source_event_records_evidence(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    event_id = _append_event(database, "one", "my paper is due Friday")
    graph = MemoryGraph(database)

    memory = graph.remember("Paper due Friday.", source_event_id=event_id)

    evidence = graph.evidence_for("memory", memory.id)
    assert len(evidence) == 1
    assert evidence[0].source_event_id == event_id
    assert evidence[0].subject_kind == "memory"
    assert evidence[0].excerpt_hash is not None


def test_remember_without_a_source_event_records_no_evidence(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)

    memory = graph.remember("No provenance for this one.")

    assert graph.evidence_for("memory", memory.id) == []


def test_supersede_carries_evidence_forward_to_the_replacement(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    event_id = _append_event(database, "two", "brief time is 7am")
    graph = MemoryGraph(database)
    original = graph.remember("Preferred brief time is 7 AM.", source_event_id=event_id)

    replacement = graph.supersede_memory(original.id, "Preferred brief time is 8 AM.")

    original_evidence = graph.evidence_for("memory", original.id)
    replacement_evidence = graph.evidence_for("memory", replacement.id)
    assert [item.source_event_id for item in original_evidence] == [event_id]
    assert [item.source_event_id for item in replacement_evidence] == [event_id]


def test_entity_and_relationship_creation_record_evidence(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    entity_event = _append_event(database, "three", "I work on Alfred Capstone")
    relation_event = _append_event(database, "four", "Nico works on Alfred Capstone")
    graph = MemoryGraph(database)
    owner = graph.ensure_self("Nico")
    project = graph.create_entity(entity_type="project", label="Alfred Capstone", source_event_id=entity_event)

    relationship = graph.add_relationship(
        source_entity_id=owner.id,
        predicate="works_on",
        target_entity_id=project.id,
        source_event_id=relation_event,
    )

    assert [item.source_event_id for item in graph.evidence_for("entity", project.id)] == [entity_event]
    assert [item.source_event_id for item in graph.evidence_for("relationship", relationship.id)] == [relation_event]
    # ensure_self was called with no source event, so it stays unevidenced.
    assert graph.evidence_for("entity", owner.id) == []


def test_add_alias_makes_an_entity_findable_by_its_alternate_name(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    entity = graph.create_entity(entity_type="person", label="Alexander")

    graph.add_alias(entity.id, "Alex")

    assert [alias.alias for alias in graph.aliases_for(entity.id)] == ["Alex"]
    found = graph.search("Alex")
    assert [item.id for item in found.entities] == [entity.id]
    assert AuditLog(database).verify() is True


def test_add_alias_rejects_an_unknown_entity(tmp_path: Path) -> None:
    graph = MemoryGraph(Database(tmp_path / "alfred.db"))

    with pytest.raises(GraphError, match="does not exist"):
        graph.add_alias("missing-entity", "Alex")


def test_add_alias_rejects_an_empty_alias(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    entity = graph.create_entity(entity_type="person", label="Alexander")

    with pytest.raises(GraphError, match="cannot be empty"):
        graph.add_alias(entity.id, "   ")


def test_multiple_aliases_are_all_searchable_and_re_adding_one_does_not_duplicate(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    entity = graph.create_entity(entity_type="person", label="Alexander")

    graph.add_alias(entity.id, "Alex")
    graph.add_alias(entity.id, "Xander")
    graph.add_alias(entity.id, "Alex")  # re-adding the same alias is idempotent

    assert sorted(alias.alias for alias in graph.aliases_for(entity.id)) == ["Alex", "Xander"]
    assert [item.id for item in graph.search("Xander").entities] == [entity.id]
    assert [item.id for item in graph.search("Alex").entities] == [entity.id]


def test_source_event_query_and_approval_frozen_bulk_forget(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            event = EventStore.append(
                connection,
                source="test",
                external_id="source-one",
                occurred_at=datetime(2026, 8, 11, tzinfo=UTC),
                content="source one",
                metadata={},
            )
    first = graph.remember("First derived fact.", source_event_id=event.id)
    second = graph.remember("Second derived fact.", source_event_id=event.id)
    unrelated = graph.remember("Independent fact.")

    assert [memory.id for memory in graph.memories_by_source_event(event.id)] == [first.id, second.id]
    actions = MemoryActions(database, ApprovalService(database))
    proposal = actions.propose_forget_by_source_event(event.id, actor="nico", reason="remove imported source")
    assert proposal.preview["memory_ids"] == [first.id, second.id]

    # This arrives after the preview and therefore cannot be included in the approval's delete scope.
    later = graph.remember("Later derived fact.", source_event_id=event.id)
    issued = actions.approvals.approve(proposal.id, actor="nico")
    receipt = actions.execute_forget_by_source_event(proposal.id, actor="nico", token=issued.token)

    assert receipt.memory_ids == [first.id, second.id]
    assert [memory.id for memory in graph.memories_by_source_event(event.id)] == [later.id]
    assert graph.get_memory(unrelated.id).status == "confirmed"
    assert [memory.id for memory in graph.memories_by_source_event(event.id, include_deleted=True)] == [first.id, second.id, later.id]
    replay = actions.execute_forget_by_source_event(proposal.id, actor="nico", token=issued.token)
    assert replay.replayed is True


def test_source_scoped_forget_does_not_consume_an_unrelated_approval(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    approvals = ApprovalService(database)
    proposal = approvals.propose(actor="nico", action_type="send_message", preview={})
    issued = approvals.approve(proposal.id, actor="nico")

    with pytest.raises(PolicyError, match="not for source-scoped"):
        MemoryActions(database, approvals).execute_forget_by_source_event(proposal.id, actor="nico", token=issued.token)

    assert approvals.get(proposal.id).state == "approved"
