"""Typed, temporal personal-memory graph stored entirely in local SQLite."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .audit import AuditEvent, AuditLog
from .db import Database

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


class SearchResult(BaseModel):
    entities: list[Entity] = Field(default_factory=list)
    memories: list[Memory] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)


class GraphError(ValueError):
    """Raised when an operation violates Alfred's graph invariants."""


class MemoryGraph:
    """The authoritative structured layer above raw local events and documents."""

    def __init__(self, database: Database) -> None:
        self.database = database

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
                self._audit(connection, actor, "memory_create", {"memory_id": memory.id, "kind": kind})
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
                self._audit(connection, actor, "memory_supersede", {"old_memory_id": memory_id, "new_memory_id": replacement_id})
                return replacement

    def search(self, query: str, *, limit: int = 8) -> SearchResult:
        """Use local FTS anchors, then one active graph hop as a compact context pack."""
        match_query = self._fts_query(query)
        if not match_query:
            return SearchResult()
        self.database.migrate()
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
            entities = self._entities_by_ids(connection, entity_ids)
            memories = self._memories_by_ids(connection, [row["memory_id"] for row in memory_rows])
            relationships = self._active_relationship_hop(connection, entity_ids, limit)
        return SearchResult(entities=entities, memories=memories, relationships=relationships)

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

    def profile(self) -> tuple[Entity | None, list[Relationship]]:
        """Return the owner node and its current outgoing relationships."""
        self.database.migrate()
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM entities WHERE entity_type = 'self'").fetchone()
            if row is None:
                return None, []
            entity = self._entity_from_row(row)
            relationships = self._active_relationship_hop(connection, [entity.id], 16, outgoing_only=True)
        return entity, relationships

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
        terms = re.findall(r"[\w]+", query, flags=re.UNICODE)
        return " AND ".join(f'"{term}"' for term in terms)

    def _entities_by_ids(self, connection: sqlite3.Connection, ids: list[str]) -> list[Entity]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(f"SELECT * FROM entities WHERE id IN ({placeholders})", ids).fetchall()
        by_id = {row["id"]: self._entity_from_row(row) for row in rows}
        return [by_id[item_id] for item_id in ids if item_id in by_id]

    def _memories_by_ids(self, connection: sqlite3.Connection, ids: list[str]) -> list[Memory]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"SELECT * FROM memories WHERE id IN ({placeholders}) AND status NOT IN ('deleted', 'rejected')",
            ids,
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
    ) -> list[Relationship]:
        if not entity_ids:
            return []
        placeholders = ",".join("?" for _ in entity_ids)
        condition = f"source_entity_id IN ({placeholders})" if outgoing_only else f"(source_entity_id IN ({placeholders}) OR target_entity_id IN ({placeholders}))"
        params = entity_ids if outgoing_only else [*entity_ids, *entity_ids]
        rows = connection.execute(
            f"SELECT * FROM relationships WHERE valid_to IS NULL AND {condition} ORDER BY valid_from DESC LIMIT ?",
            [*params, limit],
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
        )

    @staticmethod
    def _audit(connection: sqlite3.Connection, actor: str, tool: str, result: dict[str, str]) -> None:
        AuditLog.append_in_transaction(
            connection,
            AuditEvent(actor=actor, client="memory", tool=tool, outcome="ok", result=result),
        )
