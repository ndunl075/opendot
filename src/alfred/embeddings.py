"""Local vector embeddings: a pluggable provider plus a versioned sqlite-vec index.

This is additive to keyword search, never a replacement. Callers that never
configure an ``EmbeddingProvider`` see no behavior change and depend on no
running model; hybrid retrieval is opt-in, matching the cost-control rule
that cloud/model calls are never on the default read path.
"""

from __future__ import annotations

import struct
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

import httpx

from .db import Database


class EmbeddingProvider(Protocol):
    """Anything that turns text into vectors for one named, versioned model."""

    model_name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbeddingProvider:
    """Default local-first provider backed by a locally running Ollama."""

    def __init__(
        self,
        *,
        model_name: str = "nomic-embed-text",
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 30.0,
    ) -> None:
        self.model_name = model_name
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Call Ollama's embeddings endpoint once per text; no batching guarantee upstream."""
        vectors: list[list[float]] = []
        with httpx.Client(timeout=self._timeout) as client:
            for text in texts:
                response = client.post(
                    f"{self._base_url}/api/embeddings",
                    json={"model": self.model_name, "prompt": text},
                )
                response.raise_for_status()
                vectors.append(response.json()["embedding"])
        return vectors


class EmbeddingIndex:
    """Stores one current vector per (subject, model) and ranks by cosine distance.

    Rows are versioned by ``model_name`` so switching or upgrading a local
    embedding model never mixes incomparable vector spaces; old vectors are
    simply orphaned under their own model name until rebuilt.
    """

    def __init__(self, database: Database, provider: EmbeddingProvider) -> None:
        self.database = database
        self.provider = provider

    def upsert(self, *, subject_kind: str, subject_id: str, text: str) -> None:
        """Embed and store the current vector for one entity or memory."""
        self.database.migrate()
        vector = self.provider.embed([text])[0]
        self._store(subject_kind=subject_kind, subject_id=subject_id, vector=vector)

    def delete(self, *, subject_kind: str, subject_id: str) -> None:
        """Drop every stored vector for one subject, across all model versions."""
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    "DELETE FROM embeddings WHERE subject_kind = ? AND subject_id = ?",
                    (subject_kind, subject_id),
                )

    def search(
        self,
        query: str,
        *,
        subject_kind: str | None = None,
        limit: int = 8,
        max_distance: float | None = 0.4,
    ) -> list[tuple[str, float]]:
        """Return (subject_id, cosine_distance) pairs for the current model, closest first.

        ``max_distance`` drops weak matches: cosine distance ranges 0 (identical
        direction) to 2 (opposite), and an unfiltered top-K would otherwise
        always return K neighbors regardless of whether any are actually
        relevant. Pass ``None`` to disable the cutoff.
        """
        query_blob = _serialize(self.provider.embed([query])[0])
        self.database.migrate()
        condition = "model_name = ?"
        params: list[object] = [self.provider.model_name]
        if subject_kind is not None:
            condition += " AND subject_kind = ?"
            params.append(subject_kind)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT subject_id, vec_distance_cosine(vector, ?) AS distance
                FROM embeddings
                WHERE {condition}
                ORDER BY distance ASC
                LIMIT ?
                """,
                [query_blob, *params, limit],
            ).fetchall()
        return [
            (row["subject_id"], row["distance"])
            for row in rows
            if max_distance is None or row["distance"] <= max_distance
        ]

    def _store(self, *, subject_kind: str, subject_id: str, vector: list[float]) -> None:
        blob = _serialize(vector)
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    INSERT INTO embeddings (id, subject_kind, subject_id, model_name, dim, vector, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(subject_kind, subject_id, model_name) DO UPDATE SET
                        dim = excluded.dim, vector = excluded.vector, created_at = excluded.created_at
                    """,
                    (str(uuid4()), subject_kind, subject_id, self.provider.model_name, len(vector), blob, now),
                )


def _serialize(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)
