from datetime import UTC, datetime, timedelta
from pathlib import Path

from alfred.db import Database
from alfred.events import EventStore
from alfred.memory_graph import MemoryGraph
from alfred.memory_learning import MemoryFeedbackStore, MemoryLearningService


def _message(database: Database, external_id: str, text: str, *, seconds: int = 0) -> str:
    database.migrate()
    with database.connect() as connection:
        with database.transaction(connection):
            return EventStore.append(
                connection,
                source="telegram",
                external_id=external_id,
                occurred_at=datetime.now(UTC) + timedelta(seconds=seconds),
                content=text,
                metadata={"chat_id": 20, "agent_deferred": True},
            ).id


def test_explicit_remember_is_immediately_confirmed_with_provenance(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    event_id = _message(database, "1", "remember that my brief should be concise")

    result = MemoryLearningService(database).run_once()

    assert (result.processed_events, result.proposed, result.promoted) == (1, 1, 1)
    found = MemoryGraph(database).search("brief concise")
    assert [item.statement for item in found.memories] == ["My brief should be concise"]
    evidence = MemoryGraph(database).evidence_for("memory", found.memories[0].id)
    assert [item.source_event_id for item in evidence] == [event_id]
    assert evidence[0].extraction_version == "rules-v1"


def test_implicit_preference_is_quarantined_until_independently_repeated(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _message(database, "1", "i prefer short answers")
    learner = MemoryLearningService(database)

    first = learner.run_once()

    assert first.created_candidates == 1
    assert MemoryGraph(database).search("short answers").memories == []

    _message(database, "2", "for this stuff i prefer short answers", seconds=1)
    second = learner.run_once()

    assert second.promoted == 1
    found = MemoryGraph(database).search("short answers")
    assert [item.statement for item in found.memories] == ["The user prefers short answers."]
    assert len(MemoryGraph(database).evidence_for("memory", found.memories[0].id)) == 2


def test_explicit_deadline_is_immediately_sourced_and_recallable(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    event_id = _message(database, "deadline", "my paper is due Friday; remind me Thursday")

    result = MemoryLearningService(database).run_once()

    assert result.promoted == 1
    found = MemoryGraph(database).search("paper Friday")
    assert [memory.statement for memory in found.memories] == ["The user's paper is due Friday."]
    assert MemoryGraph(database).evidence_for("memory", found.memories[0].id)[0].source_event_id == event_id


def test_reprocessing_is_idempotent_and_one_event_cannot_self_corroborate(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _message(database, "1", "i prefer dark mode. i prefer dark mode.")
    learner = MemoryLearningService(database)

    first = learner.run_once()
    second = learner.run_once()

    assert first.created_candidates == 1
    assert second.processed_events == 0
    with database.connect() as connection:
        count = connection.execute("SELECT observation_count FROM memory_learning_candidates").fetchone()[0]
    assert count == 1


def test_sensitive_candidates_never_auto_promote_and_secrets_are_not_stored(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    _message(database, "1", "i prefer therapy in the mornings")
    _message(database, "2", "i prefer therapy in the mornings", seconds=1)
    _message(database, "3", "remember that my password is swordfish", seconds=2)

    MemoryLearningService(database).run_once()

    assert MemoryGraph(database).search("therapy mornings", allowed_sensitivities={"sensitive"}).memories == []
    with database.connect() as connection:
        memories = connection.execute("SELECT statement, status, sensitivity FROM memories ORDER BY created_at").fetchall()
    assert [(row["statement"], row["status"], row["sensitivity"]) for row in memories] == [
        ("The user prefers therapy in the mornings.", "candidate", "sensitive")
    ]


def test_feedback_is_append_only_and_does_not_silently_rewrite_memory(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    memory = MemoryGraph(database).remember("The user prefers concise answers.")

    receipt = MemoryFeedbackStore(database).record(
        memory.id,
        query="how should i answer?",
        outcome="irrelevant",
        actor="mcp:hermes",
    )

    assert receipt["memory_id"] == memory.id
    assert MemoryGraph(database).get_memory(memory.id).status == "confirmed"
    with database.connect() as connection:
        row = connection.execute("SELECT outcome, query FROM memory_retrieval_feedback").fetchone()
    assert (row["outcome"], row["query"]) == ("irrelevant", "how should i answer?")
