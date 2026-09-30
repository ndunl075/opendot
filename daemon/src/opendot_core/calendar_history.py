"""Precomputed, evidence-linked Calendar history for fast agent retrieval.

Raw Calendar events remain authoritative and immutable. This module
builds replaceable derived rollups from them, so answering a question never
needs to scan years of connector history or ask a model to organize it first.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .audit import AuditEvent, AuditLog
from .db import Database
from .memory_graph import MemoryGraph


class CalendarRollupResult(BaseModel):
    changed: bool = False
    source_events: int = 0
    items: int = 0
    days: int = 0
    groups: int = 0


class CalendarSearchResult(BaseModel):
    groups: list[dict[str, Any]] = Field(default_factory=list)
    days: list[dict[str, Any]] = Field(default_factory=list)


_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("exam", re.compile(r"\b(?:exam|midterm|final|test)\b", re.IGNORECASE)),
    ("quiz", re.compile(r"\bquiz\b", re.IGNORECASE)),
    ("assignment", re.compile(r"\b(?:assignment|homework|problem set|project|lab|paper)\b", re.IGNORECASE)),
)
_WORDS = re.compile(r"[a-z0-9]{2,}", re.IGNORECASE)
_STOP_WORDS = frozenset(
    {"about", "and", "did", "for", "had", "has", "have", "how", "the", "was", "what", "when", "where", "with"}
)
_TERM_ALIASES: dict[str, tuple[str, ...]] = {
    "assignment": ("assignment",),
    "assignments": ("assignment",),
    "exam": ("exam", "test"),
    "exams": ("exam", "test"),
    "quiz": ("quiz",),
    "quizzes": ("quiz",),
    "test": ("exam", "test"),
    "tests": ("exam", "test"),
}


class CalendarRollupService:
    """Build compact daily and course/calendar rollups when source data changes."""

    version = "calendar-rollups-v1"

    def __init__(self, database: Database) -> None:
        self.database = database

    def rebuild_if_changed(self) -> CalendarRollupResult:
        self.database.migrate()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, source, external_id, occurred_at, content, metadata_json
                FROM events
                WHERE source = 'google_calendar'
                ORDER BY occurred_at, id
                """
            ).fetchall()
            catalog_rows = connection.execute(
                """
                SELECT record_id, payload_json FROM connector_records
                WHERE connector = 'google_calendar'
                  AND account = 'self'
                  AND record_type = 'calendar'
                """
            ).fetchall()

        fingerprint = hashlib.sha256(
            (self.version + "\n" + "\n".join(
                f"{row['id']}:{row['occurred_at']}" for row in rows
            )).encode("utf-8")
        ).hexdigest()
        with self.database.connect() as connection:
            state = connection.execute(
                "SELECT source_fingerprint FROM calendar_rollup_state WHERE singleton = 1"
            ).fetchone()
        if state and state["source_fingerprint"] == fingerprint:
            return CalendarRollupResult(changed=False, source_events=len(rows))

        calendar_labels: dict[str, str] = {}
        for row in catalog_rows:
            payload = json.loads(row["payload_json"])
            label = str(payload.get("title") or row["record_id"])
            calendar_labels[str(row["record_id"])] = label
            if payload.get("primary"):
                calendar_labels["primary"] = label

        # Connector events are versioned. Keep the newest known version for
        # each provider item before grouping, while retaining its source-event
        # id in the rollup as provenance.
        latest: dict[str, tuple[tuple[str, int, int], dict[str, Any]]] = {}
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            provider_id = metadata.get("calendar_event_id")
            calendar_id = str(metadata.get("calendar_id") or "primary")
            stable_key = f"calendar:{calendar_id}:{provider_id}"
            if not provider_id:
                continue
            version_key = (
                str(row["occurred_at"]),
                int(metadata.get("source_revision") or 0),
                int(str(row["external_id"] or "").endswith(":calendar-v2")),
            )
            existing = latest.get(stable_key)
            if existing is None or version_key >= existing[0]:
                latest[stable_key] = (version_key, {**dict(row), "metadata": metadata})

        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for stable_key, (_version, row) in latest.items():
            item = self._normalize({**row, "stable_key": stable_key}, calendar_labels)
            if item is None:
                continue
            grouped[(item["day"], item["group_key"])].append(item)

        now = datetime.now().astimezone().isoformat()
        group_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute("DELETE FROM calendar_daily_rollups")
                connection.execute("DELETE FROM calendar_group_rollups")
                for (day, group_key), items in sorted(grouped.items()):
                    items.sort(key=lambda item: (item.get("at") or "", item["title"].casefold()))
                    label = items[0]["group_label"]
                    group_items[group_key].extend(items)
                    search_text = " ".join(
                        [day, label, *(f"{item['title']} {item['item_type']}" for item in items)]
                    ).casefold()
                    connection.execute(
                        """
                        INSERT INTO calendar_daily_rollups (
                            day, group_key, group_label, items_json, search_text,
                            item_count, generated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            day,
                            group_key,
                            label,
                            json.dumps(items, sort_keys=True, separators=(",", ":")),
                            search_text,
                            len(items),
                            now,
                        ),
                    )
                for group_key, items in sorted(group_items.items()):
                    days = sorted({item["day"] for item in items})
                    counts = Counter(item["item_type"] for item in items)
                    label = items[0]["group_label"]
                    recent_titles = [item["title"] for item in sorted(items, key=lambda item: item["day"], reverse=True)[:20]]
                    stats = {"items": len(items), "types": dict(sorted(counts.items())), "recent_titles": recent_titles}
                    connection.execute(
                        """
                        INSERT INTO calendar_group_rollups (
                            group_key, group_label, first_day, last_day,
                            stats_json, search_text, generated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            group_key,
                            label,
                            days[0],
                            days[-1],
                            json.dumps(stats, sort_keys=True, separators=(",", ":")),
                            f"{label} {' '.join(counts.keys())} {' '.join(recent_titles)}".casefold(),
                            now,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO calendar_rollup_state (
                        singleton, source_fingerprint, source_event_count, generated_at
                    ) VALUES (1, ?, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        source_fingerprint = excluded.source_fingerprint,
                        source_event_count = excluded.source_event_count,
                        generated_at = excluded.generated_at
                    """,
                    (fingerprint, len(rows), now),
                )
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="system:calendar_history",
                        client="calendar_history",
                        tool="calendar_rollup",
                        outcome="ok",
                        result={
                            "source_events": len(rows),
                            "items": sum(len(items) for items in grouped.values()),
                            "days": len(grouped),
                            "groups": len(group_items),
                        },
                    ),
                )
        return CalendarRollupResult(
            changed=True,
            source_events=len(rows),
            items=sum(len(items) for items in grouped.values()),
            days=len(grouped),
            groups=len(group_items),
        )

    def search(self, query: str, *, limit: int = 6) -> CalendarSearchResult:
        """Retrieve a tiny precomputed context pack; never scan raw events."""
        self.database.migrate()
        terms: set[str] = set()
        for word in _WORDS.findall(query):
            canonical = word.casefold()
            if canonical in _STOP_WORDS:
                continue
            terms.update(_TERM_ALIASES.get(canonical, (canonical,)))
        ordered_terms = sorted(terms)
        with self.database.connect() as connection:
            groups = connection.execute(
                "SELECT * FROM calendar_group_rollups ORDER BY last_day DESC"
            ).fetchall()
            days = connection.execute(
                "SELECT * FROM calendar_daily_rollups ORDER BY day DESC"
            ).fetchall()

        def score(row: Any) -> tuple[int, str]:
            text = str(row["search_text"])
            tokens = set(word.casefold() for word in _WORDS.findall(text))
            return (sum(term in tokens for term in ordered_terms), str(row["last_day"] if "last_day" in row.keys() else row["day"]))

        ranked_groups = sorted(groups, key=score, reverse=True)
        ranked_days = sorted(days, key=score, reverse=True)
        if ordered_terms and any(score(row)[0] for row in ranked_groups + ranked_days):
            ranked_groups = [row for row in ranked_groups if score(row)[0] > 0]
            ranked_days = [row for row in ranked_days if score(row)[0] > 0]
        return CalendarSearchResult(
            groups=[
                {
                    "label": row["group_label"],
                    "first_day": row["first_day"],
                    "last_day": row["last_day"],
                    "stats": json.loads(row["stats_json"]),
                }
                for row in ranked_groups[:limit]
            ],
            days=[
                {
                    "day": row["day"],
                    "label": row["group_label"],
                    "items": json.loads(row["items_json"]),
                }
                for row in ranked_days[:limit]
            ],
        )

    @staticmethod
    def _normalize(row: dict[str, Any], calendar_labels: dict[str, str]) -> dict[str, Any] | None:
        metadata = row["metadata"]
        title = str(row.get("content") or "Untitled item")
        item_type = next((kind for kind, pattern in _TYPE_PATTERNS if pattern.search(title)), "event")
        if metadata.get("status") == "cancelled":
            return None
        value = metadata.get("start")
        calendar_id = str(metadata.get("calendar_id") or "primary")
        group_key = f"calendar:{calendar_id}"
        group_label = calendar_labels.get(calendar_id, calendar_id)
        status = str(metadata.get("status") or "scheduled")
        added_by = _identity_label(metadata.get("creator"))
        organizer = _identity_label(metadata.get("organizer"))
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone()
            day = parsed.date().isoformat()
            at = parsed.isoformat()
        except ValueError:
            return None
        return {
            "stable_key": str(row["stable_key"]),
            "source_event_id": str(row["id"]),
            "title": title,
            "day": day,
            "at": at,
            "group_key": group_key,
            "group_label": group_label,
            "item_type": item_type,
            "status": status,
            "added_by": added_by,
            "organizer": organizer,
        }


def _identity_label(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    label = value.get("displayName") or value.get("email")
    return str(label) if label else None



class CalendarMemoryResult(BaseModel):
    changed: bool = False
    groups_created: int = 0
    memories_created: int = 0
    memories_updated: int = 0
    memories_retired: int = 0
    active_items: int = 0


class CalendarMemoryService:
    """Maintain replaceable semantic memory derived from local history."""

    version = "calendar-memory-v1"

    def __init__(self, database: Database, *, owner_label: str = "OpenDot owner") -> None:
        self.database = database
        self.owner_label = owner_label

    def rebuild_if_changed(self) -> CalendarMemoryResult:
        self.database.migrate()
        self._cleanup_stale_embeddings()
        graph = MemoryGraph(self.database)
        owner = graph.ensure_self(self.owner_label, actor="system:calendar_history")
        with self.database.connect() as connection:
            state = connection.execute(
                "SELECT source_fingerprint FROM calendar_rollup_state WHERE singleton = 1"
            ).fetchone()
            if state is None:
                return CalendarMemoryResult()
            rollup_fingerprint = f"{self.version}:{state['source_fingerprint']}"
            prior = connection.execute(
                "SELECT rollup_fingerprint FROM historical_memory_state WHERE singleton = 1"
            ).fetchone()
            if prior and prior["rollup_fingerprint"] == rollup_fingerprint:
                count = connection.execute(
                    "SELECT COUNT(*) AS count FROM historical_memory_items WHERE active = 1"
                ).fetchone()["count"]
                return CalendarMemoryResult(changed=False, active_items=int(count))
            groups = connection.execute(
                "SELECT group_key, group_label, first_day, last_day, stats_json FROM calendar_group_rollups"
            ).fetchall()
            days = connection.execute(
                "SELECT group_key, group_label, items_json FROM calendar_daily_rollups ORDER BY day, group_key"
            ).fetchall()

        result = CalendarMemoryResult(changed=True)
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
                        actor="system:calendar_history",
                        client="calendar_history",
                        tool="calendar_memory_rebuild",
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
        entity_type = "calendar"
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
            ) VALUES (?, ?, ?, ?, '["calendar"]', 'personal', 1.0, 1, ?, ?)
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
        predicate = "uses_calendar"
        relationship_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO relationships (
                id, source_entity_id, predicate, target_entity_id, relation_kind,
                cardinality, valid_from, domains_json, sensitivity, confidence,
                confirmed, created_at
            ) VALUES (?, ?, ?, ?, 'state', 'multi', ?, '["calendar"]', 'personal', 1.0, 1, ?)
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
            ) VALUES (?, 'history', ?, 'confirmed', ?, ?, '["calendar"]', 'personal', 1.0, 1, ?, ?)
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
        details = [f'{day}: "{title}" was a {item_type} on the {group_label} calendar at {at}']
        if item.get("added_by"):
            details.append(f'added by {item["added_by"]}')
        if item.get("organizer") and item.get("organizer") != item.get("added_by"):
            details.append(f'organized by {item["organizer"]}')
        return "; ".join(details) + "."

    @staticmethod
    def _fingerprint(*values: str) -> str:
        return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()
