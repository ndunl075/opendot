"""Precomputed, evidence-linked academic history for fast agent retrieval.

Raw Calendar and Canvas events remain authoritative and immutable. This module
builds replaceable derived rollups from them, so answering a question never
needs to scan years of connector history or ask a model to organize it first.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .audit import AuditEvent, AuditLog
from .db import Database


class AcademicRollupResult(BaseModel):
    changed: bool = False
    source_events: int = 0
    items: int = 0
    days: int = 0
    groups: int = 0


class AcademicSearchResult(BaseModel):
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


class AcademicMemoryService:
    """Build compact daily and course/calendar rollups when source data changes."""

    version = "academic-rollups-v3"

    def __init__(self, database: Database) -> None:
        self.database = database

    def rebuild_if_changed(self) -> AcademicRollupResult:
        self.database.migrate()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, source, external_id, occurred_at, content, metadata_json
                FROM events
                WHERE source IN ('google_calendar', 'canvas')
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
                "SELECT source_fingerprint FROM academic_rollup_state WHERE singleton = 1"
            ).fetchone()
        if state and state["source_fingerprint"] == fingerprint:
            return AcademicRollupResult(changed=False, source_events=len(rows))

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
        latest: dict[str, tuple[tuple[str, int], dict[str, Any]]] = {}
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            if row["source"] == "google_calendar":
                provider_id = metadata.get("calendar_event_id")
                calendar_id = str(metadata.get("calendar_id") or "primary")
                stable_key = f"calendar:{calendar_id}:{provider_id}"
            else:
                provider_id = metadata.get("assignment_id")
                course_id = metadata.get("course_id")
                stable_key = f"canvas:{course_id}:{provider_id}" if course_id else f"canvas:{provider_id}"
            if not provider_id:
                continue
            version_key = (
                str(row["occurred_at"]),
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
                connection.execute("DELETE FROM academic_daily_rollups")
                connection.execute("DELETE FROM academic_group_rollups")
                for (day, group_key), items in sorted(grouped.items()):
                    items.sort(key=lambda item: (item.get("at") or "", item["title"].casefold()))
                    label = items[0]["group_label"]
                    group_items[group_key].extend(items)
                    search_text = " ".join(
                        [day, label, *(f"{item['title']} {item['item_type']}" for item in items)]
                    ).casefold()
                    connection.execute(
                        """
                        INSERT INTO academic_daily_rollups (
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
                        INSERT INTO academic_group_rollups (
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
                    INSERT INTO academic_rollup_state (
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
                        actor="system:academic_memory",
                        client="academic_memory",
                        tool="academic_rollup",
                        outcome="ok",
                        result={
                            "source_events": len(rows),
                            "items": sum(len(items) for items in grouped.values()),
                            "days": len(grouped),
                            "groups": len(group_items),
                        },
                    ),
                )
        return AcademicRollupResult(
            changed=True,
            source_events=len(rows),
            items=sum(len(items) for items in grouped.values()),
            days=len(grouped),
            groups=len(group_items),
        )

    def search(self, query: str, *, limit: int = 6) -> AcademicSearchResult:
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
                "SELECT * FROM academic_group_rollups ORDER BY last_day DESC"
            ).fetchall()
            days = connection.execute(
                "SELECT * FROM academic_daily_rollups ORDER BY day DESC"
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
        return AcademicSearchResult(
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
        if row["source"] == "google_calendar":
            if metadata.get("status") == "cancelled":
                return None
            value = metadata.get("start")
            calendar_id = str(metadata.get("calendar_id") or "primary")
            group_key = f"calendar:{calendar_id}"
            group_label = calendar_labels.get(calendar_id, calendar_id)
            status = str(metadata.get("status") or "scheduled")
            added_by = _identity_label(metadata.get("creator"))
            organizer = _identity_label(metadata.get("organizer"))
        else:
            value = metadata.get("due_at")
            group_label = str(metadata.get("course_name") or "Canvas")
            group_key = f"canvas:{group_label.casefold()}"
            status = str(metadata.get("kind") or "assignment")
            if metadata.get("submission_status"):
                status = str(metadata["submission_status"])
            if item_type == "event":
                item_type = "assignment"
            added_by = None
            organizer = None
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
