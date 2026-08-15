"""Typed, temporal personal-memory graph stored entirely in local SQLite."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .audit import AuditEvent, AuditLog
from .db import Database
from .embeddings import EmbeddingIndex, EmbeddingProvider
from .policy import Approval, ApprovalService, PolicyError

Sensitivity = Literal["public", "personal", "sensitive", "secret"]
MemoryStatus = Literal["candidate", "confirmed", "superseded", "rejected", "deleted"]
RelationKind = Literal["state", "event"]
Cardinality = Literal["single", "multi"]


class Entity(BaseModel):
    id: str
    entity_type: str
    label: str
    properties: dict[str, Any]
    domains: list[str]
    sensitivity: Sensitivity
    confidence: float
    confirmed: bool
    created_at: datetime
    updated_at: datetime


class Relationship(BaseModel):
    id: str
    source_entity_id: str
    predicate: str
    target_entity_id: str
    relation_kind: RelationKind
    cardinality: Cardinality
    valid_from: datetime
    valid_to: datetime | None
    domains: list[str]
    sensitivity: Sensitivity
    confidence: float
    confirmed: bool


class Memory(BaseModel):
    id: str
    kind: str
    statement: str
    status: MemoryStatus
    source_event_id: str | None
    supersedes_memory_id: str | None
    confidence: float
    confirmed: bool
    sensitivity: Sensitivity


class Evidence(BaseModel):
    id: str
    subject_kind: str
    subject_id: str
    source_event_id: str | None
    document_reference: str | None
    source_account: str | None
    extraction_version: str | None
    excerpt_hash: str | None
    created_at: datetime


class Alias(BaseModel):
    entity_id: str
    alias: str
    source: str
    confidence: float


class SearchResult(BaseModel):
    entities: list[Entity] = Field(default_factory=list)
    memories: list[Memory] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)


class GraphError(ValueError):
    """Raised when an operation violates Alfred's graph invariants."""


class MemoryGraph:
    """The authoritative structured layer above raw local events and documents."""

    def __init__(self, database: Database, *, embedding_provider: EmbeddingProvider | None = None) -> None:
        """``embedding_provider`` is opt-in; without it, search stays FTS-only as before."""
        self.database = database
        self._embedding_index = EmbeddingIndex(database, embedding_provider) if embedding_provider else None

    def ensure_self(self, label: str, *, actor: str = "user:cli") -> Entity:
        """Create the one permanent owner identity, or return the existing one."""
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                existing = connection.execute("SELECT * FROM entities WHERE entity_type = 'self'").fetchone()
                if existing:
                    return self._entity_from_row(existing)
                entity = self._create_entity(
                    connection,
                    entity_type="self",
                    label=label,
                    properties={},
                    domains=[],
                    sensitivity="personal",
                    confidence=1.0,
                    confirmed=True,
                )
                self._audit(connection, actor, "entity_create", {"entity_id": entity.id, "type": "self"})
                return entity

    def create_entity(
        self,
        *,
        entity_type: str,
        label: str,
        properties: dict[str, Any] | None = None,
        domains: list[str] | None = None,
        sensitivity: Sensitivity = "personal",
        confidence: float = 1.0,
        confirmed: bool = True,
        source_event_id: str | None = None,
        actor: str = "user:cli",
    ) -> Entity:
        """Create a durable named entity only from a confirmed registry type."""
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                entity = self._create_entity(
                    connection,
                    entity_type=entity_type,
                    label=label,
                    properties=properties or {},
                    domains=domains or [],
                    sensitivity=sensitivity,
                    confidence=confidence,
                    confirmed=confirmed,
                )
                self._record_evidence(
                    connection,
                    subject_kind="entity",
                    subject_id=entity.id,
                    source_event_id=source_event_id,
                    excerpt=label,
                )
                self._audit(connection, actor, "entity_create", {"entity_id": entity.id, "type": entity_type})
                return entity

    def add_relationship(
        self,
        *,
        source_entity_id: str,
        predicate: str,
        target_entity_id: str,
        relation_kind: RelationKind | None = None,
        cardinality: Cardinality | None = None,
        valid_from: datetime | None = None,
        domains: list[str] | None = None,
        sensitivity: Sensitivity = "personal",
        confidence: float = 1.0,
        confirmed: bool = True,
        source_event_id: str | None = None,
        actor: str = "user:cli",
    ) -> Relationship:
        """Record a typed relationship and close a replaced single-valued state."""
        self.database.migrate()
        starts_at = (valid_from or datetime.now(UTC)).astimezone(UTC)
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                self._require_entity(connection, source_entity_id)
                self._require_entity(connection, target_entity_id)
                registry = connection.execute(
                    "SELECT * FROM relation_registry WHERE predicate = ? AND enabled = 1 AND confirmed = 1",
                    (predicate,),
                ).fetchone()
                if registry is None:
                    raise GraphError(f"relationship predicate is not confirmed: {predicate}")
                kind = relation_kind or registry["default_kind"]
                multiplicity = cardinality or registry["default_cardinality"]
                if kind == "state" and multiplicity == "single":
                    connection.execute(
                        """
                        UPDATE relationships
                        SET valid_to = ?
                        WHERE source_entity_id = ? AND predicate = ? AND relation_kind = 'state'
                          AND cardinality = 'single' AND valid_to IS NULL
                        """,
                        (starts_at.isoformat(), source_entity_id, predicate),
                    )
                relationship_id = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO relationships (
                        id, source_entity_id, predicate, target_entity_id, relation_kind,
                        cardinality, valid_from, domains_json, sensitivity, confidence, confirmed, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        relationship_id,
                        source_entity_id,
                        predicate,
                        target_entity_id,
                        kind,
                        multiplicity,
                        starts_at.isoformat(),
                        self._json(domains or []),
                        sensitivity,
                        confidence,
                        int(confirmed),
                        datetime.now(UTC).isoformat(),
                    ),
                )
                row = connection.execute("SELECT * FROM relationships WHERE id = ?", (relationship_id,)).fetchone()
                relationship = self._relationship_from_row(row)
                self._record_evidence(
                    connection,
                    subject_kind="relationship",
                    subject_id=relationship.id,
                    source_event_id=source_event_id,
                    excerpt=f"{source_entity_id} {predicate} {target_entity_id}",
                )
                self._audit(
                    connection,
                    actor,
                    "relationship_create",
                    {"relationship_id": relationship.id, "predicate": predicate},
                )
                return relationship

    def remember(
        self,
        statement: str,
        *,
        kind: str = "note",
        source_event_id: str | None = None,
        status: MemoryStatus = "confirmed",
        confidence: float = 1.0,
        confirmed: bool = True,
        domains: list[str] | None = None,
        sensitivity: Sensitivity = "personal",
        actor: str = "user:cli",
    ) -> Memory:
        """Store a provenance-ready memory and index it for local keyword search."""
        normalized_statement = statement.strip()
        if not normalized_statement:
            raise GraphError("memory statement cannot be empty")
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                memory_id = str(uuid4())
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    """
                    INSERT INTO memories (
                        id, kind, statement, status, source_event_id, domains_json, sensitivity,
                        confidence, confirmed, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory_id,
                        kind,
                        normalized_statement,
                        status,
                        source_event_id,
                        self._json(domains or []),
                        sensitivity,
                        confidence,
                        int(confirmed),
                        now,
                        now,
                    ),
                )
                connection.execute("INSERT INTO memory_fts (memory_id, statement) VALUES (?, ?)", (memory_id, normalized_statement))
                row = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
                memory = self._memory_from_row(row)
                self._record_evidence(
                    connection,
                    subject_kind="memory",
                    subject_id=memory.id,
                    source_event_id=source_event_id,
                    excerpt=normalized_statement,
                )
                self._audit(connection, actor, "memory_create", {"memory_id": memory.id, "kind": kind})
        if self._embedding_index is not None:
            self._embedding_index.upsert(subject_kind="memory", subject_id=memory.id, text=normalized_statement)
        return memory

    def supersede_memory(
        self,
        memory_id: str,
        replacement_statement: str,
        *,
        actor: str = "user:cli",
    ) -> Memory:
        """Preserve the old statement while creating a corrected replacement."""
        normalized_statement = replacement_statement.strip()
        if not normalized_statement:
            raise GraphError("replacement statement cannot be empty")
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                existing = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
                if existing is None:
                    raise GraphError(f"memory does not exist: {memory_id}")
                if existing["status"] in {"superseded", "deleted"}:
                    raise GraphError("only active memories can be superseded")
                now = datetime.now(UTC).isoformat()
                connection.execute("UPDATE memories SET status = 'superseded', valid_to = ?, updated_at = ? WHERE id = ?", (now, now, memory_id))
                connection.execute(
                    "INSERT INTO memory_history (id, memory_id, previous_status, next_status, actor, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid4()), memory_id, existing["status"], "superseded", actor, "superseded by corrected memory", now),
                )
                replacement_id = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO memories (
                        id, kind, statement, status, source_event_id, supersedes_memory_id,
                        valid_from, domains_json, sensitivity, confidence, confirmed, created_at, updated_at
                    ) VALUES (?, ?, ?, 'confirmed', ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        replacement_id,
                        existing["kind"],
                        normalized_statement,
                        existing["source_event_id"],
                        memory_id,
                        now,
                        existing["domains_json"],
                        existing["sensitivity"],
                        existing["confidence"],
                        now,
                        now,
                    ),
                )
                connection.execute("INSERT INTO memory_fts (memory_id, statement) VALUES (?, ?)", (replacement_id, normalized_statement))
                row = connection.execute("SELECT * FROM memories WHERE id = ?", (replacement_id,)).fetchone()
                replacement = self._memory_from_row(row)
                self._record_evidence(
                    connection,
                    subject_kind="memory",
                    subject_id=replacement.id,
                    source_event_id=existing["source_event_id"],
                    excerpt=normalized_statement,
                )
                self._audit(connection, actor, "memory_supersede", {"old_memory_id": memory_id, "new_memory_id": replacement_id})
        if self._embedding_index is not None:
            self._embedding_index.upsert(subject_kind="memory", subject_id=replacement.id, text=normalized_statement)
        return replacement

    def forget_memory(
        self,
        memory_id: str,
        *,
        reason: str = "user requested deletion",
        actor: str = "user:cli",
    ) -> Memory:
        """Scoped deletion of one memory: tombstone it, but keep raw evidence intact."""
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                existing = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
                if existing is None:
                    raise GraphError(f"memory does not exist: {memory_id}")
                if existing["status"] == "deleted":
                    raise GraphError("memory is already deleted")
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    "UPDATE memories SET status = 'deleted', valid_to = ?, updated_at = ? WHERE id = ?",
                    (now, now, memory_id),
                )
                connection.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
                connection.execute(
                    "INSERT INTO memory_history (id, memory_id, previous_status, next_status, actor, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid4()), memory_id, existing["status"], "deleted", actor, reason, now),
                )
                row = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
                memory = self._memory_from_row(row)
                self._audit(connection, actor, "memory_forget", {"memory_id": memory_id, "reason": reason})
        if self._embedding_index is not None:
            self._embedding_index.delete(subject_kind="memory", subject_id=memory_id)
        return memory

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        allowed_sensitivities: set[str] | None = None,
        include_vectors: bool = True,
    ) -> SearchResult:
        """Use local FTS anchors plus, when configured, vector recall, then one active graph hop.

        Keyword matches are trusted first since bm25 ranks exact-term precision;
        vector hits fill in only the remaining budget, catching paraphrases FTS
        would miss. Without an embedding provider this is unchanged FTS-only search.
        """
        match_query = self._fts_query(query)
        if not match_query:
            return SearchResult()
        sensitivities = {"public", "personal"} if allowed_sensitivities is None else allowed_sensitivities
        if not sensitivities:
            return SearchResult()
        self.database.migrate()
        vector_memory_ids: list[str] = []
        if include_vectors and self._embedding_index is not None:
            vector_memory_ids = [
                subject_id for subject_id, _distance in self._embedding_index.search(query, subject_kind="memory", limit=limit)
            ]
        with self.database.connect() as connection:
            entity_rows = connection.execute(
                "SELECT entity_id FROM entity_fts WHERE entity_fts MATCH ? ORDER BY bm25(entity_fts) LIMIT ?",
                (match_query, limit),
            ).fetchall()
            memory_rows = connection.execute(
                "SELECT memory_id FROM memory_fts WHERE memory_fts MATCH ? ORDER BY bm25(memory_fts) LIMIT ?",
                (match_query, limit),
            ).fetchall()
            entity_ids = [row["entity_id"] for row in entity_rows]
            memory_ids = [row["memory_id"] for row in memory_rows]
            for subject_id in vector_memory_ids:
                if subject_id not in memory_ids and len(memory_ids) < limit:
                    memory_ids.append(subject_id)
            memory_ids = self._rank_with_feedback(connection, memory_ids)
            entities = self._entities_by_ids(connection, entity_ids, sensitivities)
            memories = self._memories_by_ids(connection, memory_ids, sensitivities)
            relationships = self._active_relationship_hop(connection, [entity.id for entity in entities], limit, sensitivities=sensitivities)
        return SearchResult(entities=entities, memories=memories, relationships=relationships)

    @staticmethod
    def _rank_with_feedback(connection: sqlite3.Connection, memory_ids: list[str]) -> list[str]:
        """Let explicit retrieval feedback improve future ordering.

        Keyword/vector relevance still chooses candidates. Feedback only
        reorders that bounded set, so an unrelated frequently-liked memory can
        never enter a query on popularity alone.
        """
        if not memory_ids:
            return []
        placeholders = ",".join("?" for _ in memory_ids)
        rows = connection.execute(
            f"""
            SELECT memory_id,
                   SUM(CASE outcome WHEN 'relevant' THEN 2 WHEN 'irrelevant' THEN -1 WHEN 'incorrect' THEN -5 ELSE 0 END) AS score
            FROM memory_retrieval_feedback
            WHERE memory_id IN ({placeholders})
            GROUP BY memory_id
            """,
            memory_ids,
        ).fetchall()
        scores = {str(row["memory_id"]): int(row["score"] or 0) for row in rows}
        original = {memory_id: index for index, memory_id in enumerate(memory_ids)}
        return sorted(memory_ids, key=lambda memory_id: (-scores.get(memory_id, 0), original[memory_id]))

    def get_entity(self, entity_id: str) -> Entity | None:
        """Load one entity for a controlled projection or client response."""
        self.database.migrate()
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
        return self._entity_from_row(row) if row else None

    def get_memory(self, memory_id: str) -> Memory | None:
        """Load one memory without exposing its raw source archive."""
        self.database.migrate()
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._memory_from_row(row) if row else None

    def memories_by_source_event(self, source_event_id: str, *, include_deleted: bool = False) -> list[Memory]:
        """Return memories derived directly from one immutable source event.

        This is deliberately a provenance query rather than a text search. It is
        used to make export and deletion scopes inspectable and deterministic.
        """
        self.database.migrate()
        condition = "" if include_deleted else "AND status != 'deleted'"
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM memories WHERE source_event_id = ? {condition} ORDER BY created_at, id",
                (source_event_id,),
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def memories_in_range(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        include_deleted: bool = False,
    ) -> list[Memory]:
        """Return memories recorded in a half-open ``[since, until)`` window.

        Filters on ``created_at`` -- when Alfred recorded the claim -- not
        ``valid_from``, which is when the fact itself became true. "Export
        everything from last March" means what was written down then; a
        birthday recorded in March that has been true for decades belongs to
        March's receipts, not to the decade it describes.

        Half-open so adjacent windows tile without double-counting a memory
        sitting exactly on a boundary. Either bound may be omitted for an
        open-ended range.
        """
        self.database.migrate()
        clauses: list[str] = []
        parameters: list[Any] = []
        if since is not None:
            clauses.append("created_at >= ?")
            parameters.append(since.astimezone(UTC).isoformat())
        if until is not None:
            clauses.append("created_at < ?")
            parameters.append(until.astimezone(UTC).isoformat())
        if not include_deleted:
            clauses.append("status != 'deleted'")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM memories {where} ORDER BY created_at, id",
                tuple(parameters),
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def forget_memories_by_source_event(
        self,
        source_event_id: str,
        memory_ids: list[str],
        *,
        reason: str,
        actor: str,
    ) -> list[Memory]:
        """Tombstone an approval-frozen subset of one source event atomically."""
        if not memory_ids:
            raise GraphError("no memories were selected for deletion")
        if len(memory_ids) != len(set(memory_ids)):
            raise GraphError("memory deletion scope contains duplicate IDs")
        self.database.migrate()
        placeholders = ",".join("?" for _ in memory_ids)
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                rows = connection.execute(
                    f"SELECT * FROM memories WHERE source_event_id = ? AND id IN ({placeholders}) ORDER BY created_at, id",
                    [source_event_id, *memory_ids],
                ).fetchall()
                if len(rows) != len(memory_ids):
                    raise GraphError("memory deletion scope no longer matches its source event")
                if any(row["status"] == "deleted" for row in rows):
                    raise GraphError("memory deletion scope includes an already deleted memory")
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    f"UPDATE memories SET status = 'deleted', valid_to = ?, updated_at = ? WHERE id IN ({placeholders})",
                    [now, now, *memory_ids],
                )
                connection.execute(f"DELETE FROM memory_fts WHERE memory_id IN ({placeholders})", memory_ids)
                for row in rows:
                    connection.execute(
                        "INSERT INTO memory_history (id, memory_id, previous_status, next_status, actor, reason, created_at) VALUES (?, ?, ?, 'deleted', ?, ?, ?)",
                        (str(uuid4()), row["id"], row["status"], actor, reason, now),
                    )
                self._audit(
                    connection,
                    actor,
                    "memory_forget_by_source_event",
                    {"source_event_id": source_event_id, "memory_count": str(len(rows)), "reason": reason},
                )
                memories = [self._memory_from_row(row) for row in connection.execute(
                    f"SELECT * FROM memories WHERE id IN ({placeholders}) ORDER BY created_at, id", memory_ids
                ).fetchall()]
        if self._embedding_index is not None:
            for memory_id in memory_ids:
                self._embedding_index.delete(subject_kind="memory", subject_id=memory_id)
        return memories

    def profile(self, *, allowed_sensitivities: set[str] | None = None) -> tuple[Entity | None, list[Relationship]]:
        """Return the owner node and its current outgoing relationships."""
        self.database.migrate()
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM entities WHERE entity_type = 'self'").fetchone()
            sensitivities = {"public", "personal"} if allowed_sensitivities is None else allowed_sensitivities
            if row is None or row["sensitivity"] not in sensitivities:
                return None, []
            entity = self._entity_from_row(row)
            relationships = self._active_relationship_hop(connection, [entity.id], 16, outgoing_only=True, sensitivities=sensitivities)
        return entity, relationships

    def evidence_for(self, subject_kind: str, subject_id: str) -> list[Evidence]:
        """Return every raw-event link recorded for one memory, entity, or relationship."""
        self.database.migrate()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM evidence WHERE subject_kind = ? AND subject_id = ? ORDER BY created_at",
                (subject_kind, subject_id),
            ).fetchall()
        return [self._evidence_from_row(row) for row in rows]

    def add_alias(
        self,
        entity_id: str,
        alias: str,
        *,
        source: str = "user:cli",
        confidence: float = 1.0,
        actor: str = "user:cli",
    ) -> Alias:
        """Add an alternate name for an entity; it becomes searchable immediately.

        entity_fts's aliases column exists precisely for this -- until now
        nothing ever wrote to it, so an entity could only ever be found by
        its exact label.
        """
        normalized = alias.strip()
        if not normalized:
            raise GraphError("alias cannot be empty")
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                self._require_entity(connection, entity_id)
                connection.execute(
                    """
                    INSERT INTO aliases (entity_id, alias, source, confidence)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(entity_id, alias) DO UPDATE SET source = excluded.source, confidence = excluded.confidence
                    """,
                    (entity_id, normalized, source, confidence),
                )
                all_aliases = [
                    row["alias"]
                    for row in connection.execute(
                        "SELECT alias FROM aliases WHERE entity_id = ? ORDER BY alias", (entity_id,)
                    )
                ]
                connection.execute(
                    "UPDATE entity_fts SET aliases = ? WHERE entity_id = ?", (" ".join(all_aliases), entity_id)
                )
                self._audit(connection, actor, "entity_alias_add", {"entity_id": entity_id, "alias": normalized})
        return Alias(entity_id=entity_id, alias=normalized, source=source, confidence=confidence)

    def resolve_entity_by_name(self, name: str) -> Entity | None:
        """Return the single entity called ``name``, or None if that is ambiguous.

        Matches the label or any recorded alias, case-insensitively. Returning
        None for *several* matches as well as for none is the point, not an
        oversight: this section's rule is that it is safer to keep two
        possible "Alex" entities than to merge two people incorrectly, and a
        caller resolving a name the user typed has no evidence with which to
        choose between them.
        """
        normalized = " ".join(name.split()).casefold()
        if not normalized:
            return None
        self.database.migrate()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT e.* FROM entities e
                LEFT JOIN aliases a ON a.entity_id = e.id
                WHERE LOWER(e.label) = ? OR LOWER(a.alias) = ?
                """,
                (normalized, normalized),
            ).fetchall()
        if len(rows) != 1:
            return None
        return self._entity_from_row(rows[0])

    def record_entity_mention(
        self,
        entity_id: str,
        *,
        source_event_id: str,
        excerpt: str,
        actor: str = "user:vault",
    ) -> None:
        """Record that a source event explicitly named this entity.

        Provenance, not a claim: it says "this note mentioned this entity",
        which is exactly what a ``[[wiki link]]`` the owner typed asserts. It
        deliberately does not create a relationship -- what the mention
        *means* ("works with", "is about") is a typed, registry-validated
        edge, and inventing a predicate from a bare link would be the kind of
        guessing this section's rules exist to prevent.
        """
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                self._require_entity(connection, entity_id)
                self._record_evidence(
                    connection,
                    subject_kind="entity",
                    subject_id=entity_id,
                    source_event_id=source_event_id,
                    excerpt=excerpt,
                )
                self._audit(
                    connection,
                    actor,
                    "entity_mention_record",
                    {"entity_id": entity_id, "source_event_id": source_event_id},
                )

    def aliases_for(self, entity_id: str) -> list[Alias]:
        """Return every alternate name recorded for one entity."""
        self.database.migrate()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT entity_id, alias, source, confidence FROM aliases WHERE entity_id = ? ORDER BY alias",
                (entity_id,),
            ).fetchall()
        return [Alias(entity_id=row["entity_id"], alias=row["alias"], source=row["source"], confidence=row["confidence"]) for row in rows]

    def _create_entity(
        self,
        connection: sqlite3.Connection,
        *,
        entity_type: str,
        label: str,
        properties: dict[str, Any],
        domains: list[str],
        sensitivity: Sensitivity,
        confidence: float,
        confirmed: bool,
    ) -> Entity:
        if not label.strip():
            raise GraphError("entity label cannot be empty")
        registry = connection.execute(
            "SELECT name FROM type_registry WHERE name = ? AND enabled = 1 AND confirmed = 1",
            (entity_type,),
        ).fetchone()
        if registry is None:
            raise GraphError(f"entity type is not confirmed: {entity_type}")
        now = datetime.now(UTC).isoformat()
        entity_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO entities (
                id, entity_type, label, properties_json, domains_json, sensitivity,
                confidence, confirmed, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (entity_id, entity_type, label.strip(), self._json(properties), self._json(domains), sensitivity, confidence, int(confirmed), now, now),
        )
        connection.execute("INSERT INTO entity_fts (entity_id, label, aliases) VALUES (?, ?, '')", (entity_id, label.strip()))
        row = connection.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
        return self._entity_from_row(row)

    @staticmethod
    def _require_entity(connection: sqlite3.Connection, entity_id: str) -> None:
        if connection.execute("SELECT 1 FROM entities WHERE id = ?", (entity_id,)).fetchone() is None:
            raise GraphError(f"entity does not exist: {entity_id}")

    @staticmethod
    def _fts_query(query: str) -> str:
        # Natural questions contain glue words and generic request verbs that
        # should not make every memory term mandatory ("how should you write
        # status updates" should anchor on "status updates"). Meaningful terms
        # remain conjunctive to avoid returning "Before backup" for an exact
        # query about "After backup".
        stop_words = {
            "a", "about", "an", "and", "are", "do", "for", "how", "i", "in", "is",
            "it", "me", "my", "of", "on", "or", "should", "the", "to", "what",
            "answer", "respond", "say", "tell", "use", "when", "where", "who",
            "why", "write", "you", "your",
        }
        terms = [
            term
            for term in re.findall(r"[\w]+", query.casefold(), flags=re.UNICODE)
            if term not in stop_words and len(term) > 1
        ]
        return " AND ".join(f'"{term}"' for term in dict.fromkeys(terms))

    def _entities_by_ids(self, connection: sqlite3.Connection, ids: list[str], sensitivities: set[str]) -> list[Entity]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        sensitivity_placeholders = ",".join("?" for _ in sensitivities)
        rows = connection.execute(
            f"SELECT * FROM entities WHERE id IN ({placeholders}) AND sensitivity IN ({sensitivity_placeholders})",
            [*ids, *sorted(sensitivities)],
        ).fetchall()
        by_id = {row["id"]: self._entity_from_row(row) for row in rows}
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    def _memories_by_ids(self, connection: sqlite3.Connection, ids: list[str], sensitivities: set[str]) -> list[Memory]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        sensitivity_placeholders = ",".join("?" for _ in sensitivities)
        rows = connection.execute(
            # Candidates are deliberately quarantined until explicit user
            # confirmation or independent corroboration promotes them.
            f"SELECT * FROM memories WHERE id IN ({placeholders}) AND sensitivity IN ({sensitivity_placeholders}) AND status = 'confirmed'",
            [*ids, *sorted(sensitivities)],
        ).fetchall()
        by_id = {row["id"]: self._memory_from_row(row) for row in rows}
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    def _active_relationship_hop(
        self,
        connection: sqlite3.Connection,
        entity_ids: list[str],
        limit: int,
        *,
        outgoing_only: bool = False,
        sensitivities: set[str],
    ) -> list[Relationship]:
        if not entity_ids:
            return []
        placeholders = ",".join("?" for _ in entity_ids)
        condition = f"source_entity_id IN ({placeholders})" if outgoing_only else f"(source_entity_id IN ({placeholders}) OR target_entity_id IN ({placeholders}))"
        params = entity_ids if outgoing_only else [*entity_ids, *entity_ids]
        sensitivity_placeholders = ",".join("?" for _ in sensitivities)
        rows = connection.execute(
            f"SELECT * FROM relationships WHERE valid_to IS NULL AND sensitivity IN ({sensitivity_placeholders}) AND {condition} ORDER BY valid_from DESC LIMIT ?",
            [*sorted(sensitivities), *params, limit],
        ).fetchall()
        return [self._relationship_from_row(row) for row in rows]

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _entity_from_row(row: sqlite3.Row) -> Entity:
        return Entity(
            id=row["id"],
            entity_type=row["entity_type"],
            label=row["label"],
            properties=json.loads(row["properties_json"]),
            domains=json.loads(row["domains_json"]),
            sensitivity=row["sensitivity"],
            confidence=row["confidence"],
            confirmed=bool(row["confirmed"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _relationship_from_row(row: sqlite3.Row) -> Relationship:
        return Relationship(
            id=row["id"],
            source_entity_id=row["source_entity_id"],
            predicate=row["predicate"],
            target_entity_id=row["target_entity_id"],
            relation_kind=row["relation_kind"],
            cardinality=row["cardinality"],
            valid_from=datetime.fromisoformat(row["valid_from"]),
            valid_to=datetime.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
            domains=json.loads(row["domains_json"]),
            sensitivity=row["sensitivity"],
            confidence=row["confidence"],
            confirmed=bool(row["confirmed"]),
        )

    @staticmethod
    def _memory_from_row(row: sqlite3.Row) -> Memory:
        return Memory(
            id=row["id"],
            kind=row["kind"],
            statement=row["statement"],
            status=row["status"],
            source_event_id=row["source_event_id"],
            supersedes_memory_id=row["supersedes_memory_id"],
            confidence=row["confidence"],
            confirmed=bool(row["confirmed"]),
            sensitivity=row["sensitivity"],
        )

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row) -> Evidence:
        return Evidence(
            id=row["id"],
            subject_kind=row["subject_kind"],
            subject_id=row["subject_id"],
            source_event_id=row["source_event_id"],
            document_reference=row["document_reference"],
            source_account=row["source_account"],
            extraction_version=row["extraction_version"],
            excerpt_hash=row["excerpt_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _record_evidence(
        connection: sqlite3.Connection,
        *,
        subject_kind: str,
        subject_id: str,
        source_event_id: str | None,
        excerpt: str,
    ) -> None:
        """Link a derived claim back to the raw event it came from, when one is known.

        A hash of the excerpt is kept, not the excerpt itself: evidence proves
        what was derived from what without duplicating the archive.
        """
        if source_event_id is None:
            return
        connection.execute(
            """
            INSERT INTO evidence (id, subject_kind, subject_id, source_event_id, excerpt_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                subject_kind,
                subject_id,
                source_event_id,
                hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
                datetime.now(UTC).isoformat(),
            ),
        )

    @staticmethod
    def _audit(connection: sqlite3.Connection, actor: str, tool: str, result: dict[str, str]) -> None:
        AuditLog.append_in_transaction(
            connection,
            AuditEvent(actor=actor, client="memory", tool=tool, outcome="ok", result=result),
        )


class MemoryForgetReceipt(BaseModel):
    memory_id: str
    idempotency_key: str
    replayed: bool


class MemoryForgetSourceEventReceipt(BaseModel):
    source_event_id: str
    memory_ids: list[str]
    idempotency_key: str
    replayed: bool


class MemoryActions:
    """Approval-gated deletion: decision 8 classifies deleting data as strong-confirm, never unattended.

    MemoryGraph.forget_memory() itself stays an unconditional primitive --
    still directly callable by trusted internal callers like VaultImporter's
    forgotten-memory recovery path -- but the CLI and MCP surfaces route
    through here instead, so an automated client can no longer delete a
    memory in one unattended call. Mirrors GoogleCalendarActions's
    propose()/execute() shape exactly.
    """

    action_type = "memory_forget"
    source_event_action_type = "memory_forget_by_source_event"

    def __init__(self, database: Database, approvals: ApprovalService) -> None:
        self.database = database
        self.approvals = approvals
        self.graph = MemoryGraph(database)

    def propose_forget(self, memory_id: str, *, actor: str, reason: str = "user requested deletion") -> Approval:
        """Preview a deletion without touching the memory yet."""
        existing = self.graph.get_memory(memory_id)
        if existing is None:
            raise GraphError(f"memory does not exist: {memory_id}")
        preview = {"memory_id": memory_id, "reason": reason}
        return self.approvals.propose(actor=actor, action_type=self.action_type, preview=preview)

    def execute_forget(self, approval_id: str, *, actor: str, token: str) -> MemoryForgetReceipt:
        """Consume a fresh approval exactly once, then delete idempotently.

        See GoogleCalendarActions.execute() for the identical replay-before-
        consume ordering and its documented crash-window trade-off.
        """
        self.database.migrate()
        idempotency_key = f"{self.action_type}:{approval_id}"
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT actor, payload_json FROM action_receipts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if existing is not None:
            self.approvals.verify(approval_id, actor=actor, token=token)
            if existing["actor"] != actor:
                raise PolicyError("approval actor does not match the requested action")
            payload = json.loads(existing["payload_json"])
            return MemoryForgetReceipt(memory_id=payload["memory_id"], idempotency_key=idempotency_key, replayed=True)

        approval = self.approvals.consume(approval_id, actor=actor, token=token)
        preview = approval.preview
        memory = self.graph.forget_memory(preview["memory_id"], reason=preview.get("reason", "user requested deletion"), actor=actor)
        payload = {"memory_id": memory.id}
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    INSERT INTO action_receipts (idempotency_key, connector, action_type, approval_id, actor, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(idempotency_key) DO NOTHING
                    """,
                    (
                        idempotency_key,
                        "memory",
                        self.action_type,
                        approval_id,
                        actor,
                        json.dumps(payload, sort_keys=True),
                        datetime.now(UTC).isoformat(),
                    ),
                )
        return MemoryForgetReceipt(memory_id=memory.id, idempotency_key=idempotency_key, replayed=False)

    def propose_forget_by_source_event(
        self, source_event_id: str, *, actor: str, reason: str = "user requested deletion"
    ) -> Approval:
        """Preview all currently active memories derived from one source event."""
        memories = self.graph.memories_by_source_event(source_event_id)
        if not memories:
            raise GraphError("source event has no active memories to delete")
        memory_ids = [memory.id for memory in memories]
        return self.approvals.propose(
            actor=actor,
            action_type=self.source_event_action_type,
            preview={
                "source_event_id": source_event_id,
                "memory_ids": memory_ids,
                "memory_count": len(memory_ids),
                "reason": reason,
            },
        )

    def execute_forget_by_source_event(
        self, approval_id: str, *, actor: str, token: str
    ) -> MemoryForgetSourceEventReceipt:
        """Consume a source-scoped approval and tombstone its frozen target set."""
        self.database.migrate()
        idempotency_key = f"{self.source_event_action_type}:{approval_id}"
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT actor, payload_json FROM action_receipts WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        if existing is not None:
            self.approvals.verify(approval_id, actor=actor, token=token)
            if existing["actor"] != actor:
                raise PolicyError("approval actor does not match the requested action")
            payload = json.loads(existing["payload_json"])
            return MemoryForgetSourceEventReceipt(
                source_event_id=payload["source_event_id"],
                memory_ids=payload["memory_ids"],
                idempotency_key=idempotency_key,
                replayed=True,
            )

        pending_approval = self.approvals.get(approval_id)
        if pending_approval is None or pending_approval.action_type != self.source_event_action_type:
            raise PolicyError("approval is not for source-scoped memory deletion")
        approval = self.approvals.consume(approval_id, actor=actor, token=token)
        preview = approval.preview
        source_event_id = preview.get("source_event_id")
        memory_ids = preview.get("memory_ids")
        if not isinstance(source_event_id, str) or not isinstance(memory_ids, list) or not all(
            isinstance(memory_id, str) for memory_id in memory_ids
        ):
            raise PolicyError("approval preview is malformed")
        deleted = self.graph.forget_memories_by_source_event(
            source_event_id,
            memory_ids,
            reason=str(preview.get("reason", "user requested deletion")),
            actor=actor,
        )
        payload = {"source_event_id": source_event_id, "memory_ids": [memory.id for memory in deleted]}
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    INSERT INTO action_receipts (idempotency_key, connector, action_type, approval_id, actor, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(idempotency_key) DO NOTHING
                    """,
                    (
                        idempotency_key,
                        "memory",
                        self.source_event_action_type,
                        approval_id,
                        actor,
                        json.dumps(payload, sort_keys=True),
                        datetime.now(UTC).isoformat(),
                    ),
                )
        return MemoryForgetSourceEventReceipt(
            source_event_id=source_event_id, memory_ids=payload["memory_ids"], idempotency_key=idempotency_key, replayed=False
        )
