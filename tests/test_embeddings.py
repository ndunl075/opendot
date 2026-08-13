from pathlib import Path

from alfred.db import Database
from alfred.embeddings import EmbeddingBackfill, EmbeddingIndex
from alfred.memory_graph import MemoryGraph


class FakeEmbeddingProvider:
    """Deterministic stand-in: each text becomes a small hand-built vector.

    No live Ollama is required to exercise storage, versioning, and cosine
    ranking; only the semantics of ``EmbeddingProvider`` matter here.
    """

    def __init__(self, model_name: str, vectors: dict[str, list[float]]) -> None:
        self.model_name = model_name
        self._vectors = vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[text] for text in texts]


def test_search_ranks_by_cosine_distance_closest_first(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    provider = FakeEmbeddingProvider(
        "fake-v1",
        {
            "cat": [1.0, 0.0],
            "kitten": [0.9, 0.1],
            "spreadsheet": [0.0, 1.0],
            "query": [1.0, 0.0],
        },
    )
    index = EmbeddingIndex(database, provider)
    index.upsert(subject_kind="memory", subject_id="cat-memory", text="cat")
    index.upsert(subject_kind="memory", subject_id="kitten-memory", text="kitten")
    index.upsert(subject_kind="memory", subject_id="spreadsheet-memory", text="spreadsheet")

    results = index.search("query", limit=2)

    assert [subject_id for subject_id, _ in results] == ["cat-memory", "kitten-memory"]


def test_upsert_is_versioned_by_model_and_replaces_the_same_subject(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    provider_v1 = FakeEmbeddingProvider("fake-v1", {"first": [1.0, 0.0], "second": [0.0, 1.0]})
    index_v1 = EmbeddingIndex(database, provider_v1)
    index_v1.upsert(subject_kind="memory", subject_id="m1", text="first")
    index_v1.upsert(subject_kind="memory", subject_id="m1", text="second")

    with database.connect() as connection:
        rows = connection.execute("SELECT COUNT(*) FROM embeddings WHERE subject_id = 'm1'").fetchone()[0]
    assert rows == 1

    provider_v2 = FakeEmbeddingProvider("fake-v2", {"first": [1.0, 0.0]})
    index_v2 = EmbeddingIndex(database, provider_v2)
    index_v2.upsert(subject_kind="memory", subject_id="m1", text="first")

    with database.connect() as connection:
        model_names = {
            row["model_name"] for row in connection.execute("SELECT model_name FROM embeddings WHERE subject_id = 'm1'")
        }
    assert model_names == {"fake-v1", "fake-v2"}


def test_delete_removes_every_model_version_for_a_subject(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    index = EmbeddingIndex(database, FakeEmbeddingProvider("fake-v1", {"text": [1.0, 0.0]}))
    index.upsert(subject_kind="memory", subject_id="m1", text="text")

    index.delete(subject_kind="memory", subject_id="m1")

    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM embeddings WHERE subject_id = 'm1'").fetchone()[0]
    assert count == 0


def test_search_is_scoped_to_the_provider_current_model(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    old_provider = FakeEmbeddingProvider("fake-old", {"text": [1.0, 0.0]})
    EmbeddingIndex(database, old_provider).upsert(subject_kind="memory", subject_id="old-model-memory", text="text")

    new_provider = FakeEmbeddingProvider("fake-new", {"text": [1.0, 0.0], "query": [1.0, 0.0]})
    results = EmbeddingIndex(database, new_provider).search("query")

    assert results == []


def test_backfill_batches_only_confirmed_memories_missing_the_current_model(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    graph = MemoryGraph(database)
    first = graph.remember("first memory")
    second = graph.remember("second memory")
    provider = FakeEmbeddingProvider(
        "fake-v1", {"first memory": [1.0, 0.0], "second memory": [0.0, 1.0]}
    )

    embedded = EmbeddingBackfill(database, provider, batch_size=1).run()
    again = EmbeddingBackfill(database, provider, batch_size=1).run()

    assert embedded == 2
    assert again == 0
    with database.connect() as connection:
        ids = {row["subject_id"] for row in connection.execute("SELECT subject_id FROM embeddings")}
    assert ids == {first.id, second.id}
