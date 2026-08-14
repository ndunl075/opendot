"""Promote deterministic Calendar and Canvas history into durable memory.

The immutable connector event log remains authoritative. Academic rollups
choose the newest provider version and compact it by day; this service turns
those selected facts into provenance-linked memories and a tiny group graph.
It never asks a model to decide what happened and is safe to rerun.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .db import Database
from .memory_graph import MemoryGraph


class HistoricalMemoryResult(BaseModel):
    changed: bool = False
    groups_created: int = 0
    memories_created: int = 0
    memories_updated: int = 0
    memories_retired: int = 0
    active_items: int = 0


class HistoricalMemoryService:
    """Maintain replaceable semantic memory derived from local history."""

    version = "historical-memory-v1"

    def __init__(self, database: Database, *, owner_label: str = "Alfred owner") -> None:
        self.database = database
        self.owner_label = owner_label

    def rebuild_if_changed(self) -> HistoricalMemoryResult:
        self.database.migrate()
        self._cleanup_stale_embeddings()
        graph = MemoryGraph(self.database)
        owner = graph.ensure_self(self.owner_label, actor="system:historical_memory")
        with self.database.connect() as connection:
            state = connection.execute(
                "SELECT source_fingerprint FROM academic_rollup_state WHERE singleton = 1"
            ).fetchone()
            if state is None:
                return HistoricalMemoryResult()
            rollup_fingerprint = f"{self.version}:{state['source_fingerprint']}"
            prior = connection.execute(
                "SELECT rollup_fingerprint FROM historical_memory_state WHERE singleton = 1"
            ).fetchone()
            if prior and prior["rollup_fingerprint"] == rollup_fingerprint:
                count = connection.execute(
                    "SELECT COUNT(*) AS count FROM historical_memory_items WHERE active = 1"
                ).fetchone()["count"]
                return HistoricalMemoryResult(changed=False, active_items=int(count))
            groups = connection.execute(
                "SELECT group_key, group_label, first_day, last_day, stats_json FROM academic_group_rollups"
            ).fetchall()
            days = connection.execute(
                "SELECT group_key, group_label, items_json FROM academic_daily_rollups ORDER BY day, group_key"
            ).fetchall()

        result = HistoricalMemoryResult(changed=True)
        now = datetime.now(UTC).isoformat()
        active_keys: set[str] = set()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                for group in groups:
                    if self._ensure_group(connection, owner.id, dict(group), now):
                        result.groups_created += 1

                for day in days:
                    group_key = str(day["group_key"])
                    group_label = str(day["group_label"])
                    for item in json.loads(day["items_json"]):
                        stable_key = str(item["stable_key"])
                        active_keys.add(stable_key)
                        statement = self._statement(item, group_key, group_label)
                        fingerprint = self._fingerprint(statement, str(item["source_event_id"]))
                        existing = connection.execute(
                            "SELECT * FROM historical_memory_items WHERE stable_key = ?",
                            (stable_key,),
                        ).fetchone()
                        if existing and existing["source_fingerprint"] == fingerprint:
                            connection.execute(
                                "UPDATE historical_memory_items SET active = 1, updated_at = ? WHERE stable_key = ?",
                                (now, stable_key),
                            )
                            continue
                        memory_id = self._insert_memory(
                            connection,
                            statement=statement,
                            source_event_id=str(item["source_event_id"]),
                            now=now,
                        )
                        if existing:
                            connection.execute(
                                "UPDATE memories SET status = 'superseded', valid_to = ?, updated_at = ? WHERE id = ? AND status = 'confirmed'",
                                (now, now, existing["memory_id"]),
                            )
                            connection.execute("DELETE FROM memory_fts WHERE memory_id = ?", (existing["memory_id"],))
                            result.memories_updated += 1
                        else:
                            result.memories_created += 1
                        connection.execute(
                            """
                            INSERT INTO historical_memory_items (
                                stable_key, source_fingerprint, source_event_id, memory_id, active, updated_at
                            ) VALUES (?, ?, ?, ?, 1, ?)
                            ON CONFLICT(stable_key) DO UPDATE SET
                                source_fingerprint = excluded.source_fingerprint,
                                source_event_id = excluded.source_event_id,
                                memory_id = excluded.memory_id,
                                active = 1,
                                updated_at = excluded.updated_at
                            """,
                            (stable_key, fingerprint, item["source_event_id"], memory_id, now),
                        )

                existing_active = connection.execute(
                    "SELECT stable_key, memory_id FROM historical_memory_items WHERE active = 1"
                ).fetchall()
                for existing in existing_active:
                    if existing["stable_key"] in active_keys:
                        continue
                    connection.execute(
                        "UPDATE memories SET status = 'superseded', valid_to = ?, updated_at = ? WHERE id = ? AND status = 'confirmed'",
                        (now, now, existing["memory_id"]),
                    )
                    connection.execute("DELETE FROM memory_fts WHERE memory_id = ?", (existing["memory_id"],))
                    connection.execute(
                        "UPDATE historical_memory_items SET active = 0, updated_at = ? WHERE stable_key = ?",
                        (now, existing["stable_key"]),
                    )
                    result.memories_retired += 1

                connection.execute(
                    """
                    INSERT INTO historical_memory_state (singleton, rollup_fingerprint, item_count, generated_at)
                    VALUES (1, ?, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        rollup_fingerprint = excluded.rollup_fingerprint,
                        item_count = excluded.item_count,
                        generated_at = excluded.generated_at
                    """,
                    (rollup_fingerprint, len(active_keys), now),
                )
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="system:historical_memory",
                        client="historical_memory",
                        tool="historical_memory_rebuild",
                        outcome="ok",
                        result={
                            "groups_created": result.groups_created,
                            "memories_created": result.memories_created,
                            "memories_updated": result.memories_updated,
                            "memories_retired": result.memories_retired,
                            "active_items": len(active_keys),
                        },
                    ),
                )
        result.active_items = len(active_keys)
        return result

    def _cleanup_stale_embeddings(self) -> None:
        """Remove replaceable vectors after their memories are superseded."""
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    DELETE FROM embeddings
                    WHERE subject_kind = 'memory'
                      AND subject_id IN (SELECT id FROM memories WHERE status != 'confirmed')
                    """
                )

    def _ensure_group(self, connection: Any, owner_id: str, group: dict[str, Any], now: str) -> bool:
        group_key = str(group["group_key"])
        fingerprint = self._fingerprint(
            str(group["group_label"]), str(group["first_day"]), str(group["last_day"]), str(group["stats_json"])
        )
        existing = connection.execute(
            "SELECT entity_id, source_fingerprint FROM historical_group_entities WHERE group_key = ?",
            (group_key,),
        ).fetchone()
        if existing:
            if existing["source_fingerprint"] != fingerprint:
                connection.execute(
                    "UPDATE entities SET label = ?, properties_json = ?, updated_at = ? WHERE id = ?",
                    (
                        group["group_label"],
                        json.dumps(
                            {"source_key": group_key, "first_day": group["first_day"], "last_day": group["last_day"]},
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        now,
                        existing["entity_id"],
                    ),
                )
                connection.execute("DELETE FROM entity_fts WHERE entity_id = ?", (existing["entity_id"],))
                connection.execute(
                    "INSERT INTO entity_fts (entity_id, label, aliases) VALUES (?, ?, '')",
                    (existing["entity_id"], group["group_label"]),
                )
                connection.execute(
                    "UPDATE historical_group_entities SET source_fingerprint = ?, updated_at = ? WHERE group_key = ?",
                    (fingerprint, now, group_key),
                )
            return False

        entity_id = str(uuid4())
        entity_type = "course" if group_key.startswith("canvas:") else "calendar"
        properties = {
            "source_key": group_key,
            "first_day": group["first_day"],
            "last_day": group["last_day"],
        }
        connection.execute(
            """
            INSERT INTO entities (
                id, entity_type, label, properties_json, domains_json, sensitivity,
                confidence, confirmed, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '["academic"]', 'personal', 1.0, 1, ?, ?)
            """,
            (entity_id, entity_type, group["group_label"], json.dumps(properties, sort_keys=True, separators=(",", ":")), now, now),
        )
        connection.execute(
            "INSERT INTO entity_fts (entity_id, label, aliases) VALUES (?, ?, '')",
            (entity_id, group["group_label"]),
        )
        connection.execute(
            "INSERT INTO historical_group_entities (group_key, entity_id, source_fingerprint, updated_at) VALUES (?, ?, ?, ?)",
            (group_key, entity_id, fingerprint, now),
        )
        predicate = "takes_course" if entity_type == "course" else "uses_calendar"
        relationship_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO relationships (
                id, source_entity_id, predicate, target_entity_id, relation_kind,
                cardinality, valid_from, domains_json, sensitivity, confidence,
                confirmed, created_at
            ) VALUES (?, ?, ?, ?, 'state', 'multi', ?, '["academic"]', 'personal', 1.0, 1, ?)
            """,
            (relationship_id, owner_id, predicate, entity_id, now, now),
        )
        return True

    def _insert_memory(self, connection: Any, *, statement: str, source_event_id: str, now: str) -> str:
        memory_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO memories (
                id, kind, statement, status, source_event_id, valid_from,
                domains_json, sensitivity, confidence, confirmed, created_at, updated_at
            ) VALUES (?, 'history', ?, 'confirmed', ?, ?, '["academic"]', 'personal', 1.0, 1, ?, ?)
            """,
            (memory_id, statement, source_event_id, now, now, now),
        )
        connection.execute("INSERT INTO memory_fts (memory_id, statement) VALUES (?, ?)", (memory_id, statement))
        connection.execute(
            """
            INSERT INTO evidence (
                id, subject_kind, subject_id, source_event_id, extraction_version,
                excerpt_hash, created_at
            ) VALUES (?, 'memory', ?, ?, ?, ?, ?)
            """,
            (str(uuid4()), memory_id, source_event_id, self.version, hashlib.sha256(statement.encode()).hexdigest(), now),
        )
        return memory_id

    @staticmethod
    def _statement(item: dict[str, Any], group_key: str, group_label: str) -> str:
        title = str(item["title"])
        day = str(item["day"])
        at = str(item.get("at") or day)
        status = str(item.get("status") or "scheduled")
        item_type = str(item.get("item_type") or "event")
        if group_key.startswith("canvas:"):
            return f'{day}: "{title}" was a {item_type} for {group_label}; status {status}; due {at}.'
        details = [f'{day}: "{title}" was a {item_type} on the {group_label} calendar at {at}']
        if item.get("added_by"):
            details.append(f'added by {item["added_by"]}')
        if item.get("organizer") and item.get("organizer") != item.get("added_by"):
            details.append(f'organized by {item["organizer"]}')
        return "; ".join(details) + "."

    @staticmethod
    def _fingerprint(*values: str) -> str:
        return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()
