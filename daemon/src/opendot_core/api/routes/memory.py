"""Memory: search (keyword, SQLite FTS5), correct and forget (ARCHITECTURE.md section 9).

Everything goes through ``MemoryGraph``: a correction supersedes the old statement and keeps it in
the history, and forgetting tombstones the memory and drops it from the keyword index while the
audit log keeps the fact that it happened. Forgetting is the user's own action in the UI, so it is
not on the companion's deny list.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ...db import Database
from ...memory_graph import GraphError, MemoryGraph
from ..models import (
    API_VERSION,
    MemoryCorrectRequest,
    MemoryCorrectResult,
    MemoryForgetRequest,
    MemoryForgetResult,
    MemoryItem,
    MemorySearchQuery,
    MemorySearchResult,
)
from ._common import error, invalid, parse_time, read_body, read_query, reply

if TYPE_CHECKING:
    from . import ApiContext

ACTOR = "user:ui"
_SOURCE_LABELS = {
    "user": "What you told me",
    "gmail": "Gmail",
    "google_calendar": "Google Calendar",
    "github": "GitHub",
}
_VISIBLE = "('confirmed', 'candidate', 'superseded')"
_BASE = "SELECT m.*, e.source AS event_source FROM memories m LEFT JOIN events e ON e.id = m.source_event_id"


def source_label(source: str) -> str:
    return _SOURCE_LABELS.get(source, source.replace("_", " ").title())


def _people(connection: sqlite3.Connection, source_event_id: str | None) -> list[str]:
    if not source_event_id:
        return []
    rows = connection.execute(
        "SELECT DISTINCT ent.label FROM evidence ev JOIN entities ent ON ent.id = ev.subject_id "
        "WHERE ev.subject_kind = 'entity' AND ev.source_event_id = ? AND ent.entity_type = 'person' "
        "ORDER BY ent.label",
        (source_event_id,),
    ).fetchall()
    return [row["label"] for row in rows]


def _item(connection: sqlite3.Connection, row: sqlite3.Row) -> MemoryItem:
    source = row["event_source"] or "user"
    return MemoryItem(
        id=row["id"],
        statement=row["statement"],
        source=source,
        source_label=source_label(source),
        people=_people(connection, row["source_event_id"]),
        learned_at=parse_time(row["created_at"]),  # type: ignore[arg-type]
        confidence="confirmed" if row["status"] == "confirmed" or (row["status"] == "superseded" and row["confirmed"]) else "inferred",
        superseded=row["status"] == "superseded",
    )


def _person_event_ids(connection: sqlite3.Connection, person: str) -> list[str]:
    rows = connection.execute(
        "SELECT DISTINCT ev.source_event_id FROM evidence ev JOIN entities ent ON ent.id = ev.subject_id "
        "WHERE ev.subject_kind = 'entity' AND ev.source_event_id IS NOT NULL AND (ent.id = ? "
        "OR lower(ent.label) = lower(?) OR EXISTS (SELECT 1 FROM aliases a WHERE a.entity_id = ent.id "
        "AND lower(a.alias) = lower(?)))",
        (person, person, person),
    ).fetchall()
    return [row["source_event_id"] for row in rows]


def _filtered_ids(
    connection: sqlite3.Connection, *, source: str | None, person: str | None, extra: str = "", params: tuple = ()
) -> list[sqlite3.Row]:
    clauses = [f"m.status IN {_VISIBLE}"]
    args: list[Any] = []
    if source:
        clauses.append("COALESCE(e.source, 'user') = ?")
        args.append(source)
    if person:
        events = _person_event_ids(connection, person)
        if not events:
            return []
        clauses.append(f"m.source_event_id IN ({','.join('?' for _ in events)})")
        args.extend(events)
    if extra:
        clauses.append(extra)
        args.extend(params)
    return connection.execute(f"{_BASE} WHERE {' AND '.join(clauses)} ORDER BY m.created_at DESC, m.id", args).fetchall()


def search(database: Database, query: MemorySearchQuery) -> MemorySearchResult:
    graph = MemoryGraph(database)  # migrates
    with database.connect() as connection:
        rows = _filtered_ids(connection, source=query.source, person=query.person)
        if query.q.strip():
            match = graph._fts_query(query.q)  # noqa: SLF001 - the graph's own keyword-query builder
            order: dict[str, int] = {}
            if match:
                for index, hit in enumerate(
                    connection.execute(
                        "SELECT memory_id FROM memory_fts WHERE memory_fts MATCH ? ORDER BY bm25(memory_fts)", (match,)
                    ).fetchall()
                ):
                    order[hit["memory_id"]] = index
            rows = sorted((r for r in rows if r["id"] in order), key=lambda r: order[r["id"]])
        total = len(rows)
        items = [_item(connection, row) for row in rows[: query.limit]]
    return MemorySearchResult(items=items, total=total)


def _memory_item(database: Database, memory_id: str) -> MemoryItem | None:
    with database.connect() as connection:
        row = connection.execute(f"{_BASE} WHERE m.id = ?", (memory_id,)).fetchone()
        return _item(connection, row) if row else None


def forget_ids(database: Database, ids: list[str], *, reason: str, actor: str = ACTOR) -> int:
    graph = MemoryGraph(database)
    count = 0
    for memory_id in ids:
        try:
            graph.forget_memory(memory_id, reason=reason, actor=actor)
            count += 1
        except GraphError:
            continue  # already forgotten
    return count


def forget_source(database: Database, source: str, *, account: str | None = None, actor: str = ACTOR) -> int:
    """Forget every memory learned from one app (optionally one of its accounts)."""
    database.migrate()
    with database.connect() as connection:
        rows = _filtered_ids(connection, source=source, person=None)
        ids = []
        for row in rows:
            if account:
                accounts = {
                    r["source_account"]
                    for r in connection.execute(
                        "SELECT source_account FROM evidence WHERE subject_kind = 'memory' AND subject_id = ? "
                        "AND source_account IS NOT NULL",
                        (row["id"],),
                    ).fetchall()
                }
                if accounts and account not in accounts:
                    continue
            ids.append(row["id"])
    return forget_ids(database, ids, reason=f"forgot everything learned from {source}", actor=actor)


def memory_count(database: Database, source: str) -> int:
    database.migrate()
    with database.connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS n FROM memories m LEFT JOIN events e ON e.id = m.source_event_id "
            "WHERE m.status IN ('confirmed', 'candidate') AND COALESCE(e.source, 'user') = ?",
            (source,),
        ).fetchone()
    return int(row["n"])


def _forget(database: Database, body: MemoryForgetRequest) -> MemoryForgetResult | str:
    """The result, or a message when the request is missing what its scope needs."""
    database.migrate()
    scope = body.scope
    if scope == "item":
        if not body.item_id:
            return "Say which memory to forget (item_id)."
        with database.connect() as connection:
            row = connection.execute("SELECT status FROM memories WHERE id = ?", (body.item_id,)).fetchone()
        if row is None or row["status"] == "deleted":
            return "not_found"
        ids = [body.item_id]
    elif scope == "source":
        if not body.source:
            return "Say which source to forget (source)."
        with database.connect() as connection:
            ids = [r["id"] for r in _filtered_ids(connection, source=body.source, person=None)]
    elif scope == "person":
        if not body.person:
            return "Say which person to forget (person)."
        with database.connect() as connection:
            ids = [r["id"] for r in _filtered_ids(connection, source=None, person=body.person)]
    else:
        if body.since is None and body.until is None:
            return "Give a time range to forget (since and/or until)."
        memories = MemoryGraph(database).memories_in_range(since=body.since, until=body.until)
        ids = [m.id for m in memories if m.status != "rejected"]
    count = forget_ids(database, ids, reason=f"user forgot by {scope}")
    return MemoryForgetResult(forgotten_count=count, scope=scope)


def routes(ctx: ApiContext) -> list[Route]:
    database = ctx.database
    if database is None:
        return []

    async def memory_search(request: Request) -> Response:
        try:
            query: MemorySearchQuery = read_query(request, MemorySearchQuery)
        except ValidationError as problem:
            return invalid(problem)
        return reply(await asyncio.to_thread(search, database, query))

    async def memory_correct(request: Request) -> Response:
        memory_id = request.path_params["memory_id"]
        try:
            body: MemoryCorrectRequest = await read_body(request, MemoryCorrectRequest)
        except ValidationError as problem:
            return invalid(problem)
        if not body.statement.strip():
            return error(422, "invalid_request", "The corrected statement cannot be blank.")
        try:
            replacement = await asyncio.to_thread(MemoryGraph(database).supersede_memory, memory_id, body.statement, actor=ACTOR)
        except GraphError as problem:
            if "does not exist" in str(problem):
                return error(404, "not_found", "No such memory.")
            return error(409, "memory_not_active", "Only a current memory can be corrected.")
        corrected = await asyncio.to_thread(_memory_item, database, replacement.id)
        assert corrected is not None
        return reply(MemoryCorrectResult(corrected=corrected, superseded_id=memory_id))

    async def memory_forget(request: Request) -> Response:
        try:
            body: MemoryForgetRequest = await read_body(request, MemoryForgetRequest)
        except ValidationError as problem:
            return invalid(problem)
        result = await asyncio.to_thread(_forget, database, body)
        if result == "not_found":
            return error(404, "not_found", "No such memory.")
        if isinstance(result, str):
            return error(422, "invalid_request", result)
        return reply(result)

    v = f"/{API_VERSION}/memory"
    return [
        Route(v, memory_search, methods=["GET"]),
        # "forget" is registered before the {memory_id} pattern so it is never read as an id
        Route(f"{v}/forget", memory_forget, methods=["POST"]),
        Route(f"{v}/{{memory_id}}/correct", memory_correct, methods=["POST"]),
    ]
