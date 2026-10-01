"""Context packing: what a turn is told about the owner's data, within a budget."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ..db import Database
from ..memory_graph import MemoryGraph
from ..response_feedback import ResponseFeedbackService
from .mail import low_priority_mail, mail_rank

# The architecture budgets roughly 3,000 tokens of extra context. A character
# ceiling is deliberately conservative and tokenizer-independent; the current
# request and the policy preamble are outside this allowance.
DEFAULT_CONTEXT_CHAR_BUDGET = 10_000

# Keep follow-ups grounded without turning every invocation into a transcript
# dump. Matches the casual lane's depth so a multi-turn tool conversation
# doesn't lose earlier context sooner than a plain chat would; the time window
# still prevents an old topic being mistaken for the current one, and
# fit_context_budget trims from the oldest end if the pack gets too big.
CONVERSATION_LOOKBACK_SECONDS = 6 * 60 * 60
MAX_CONTEXT_EXCHANGES = 8

#: A request at or under this many words is treated as a follow-up that cannot
#: stand on its own ("yeah do that", "why?"), so it inherits the previous
#: exchange when choosing tools. Anything longer states its own topic and must
#: not pick up one from earlier in the conversation.
FOLLOW_UP_MAX_WORDS = 8
CASUAL_CONVERSATION_LOOKBACK_SECONDS = 7 * 24 * 60 * 60
CASUAL_MAX_CONTEXT_EXCHANGES = 8

#: Outbox idempotency-key prefix under which an answer's bubbles are stored,
#: one key per bubble: ``agent-reply:<external_id>:<index>``.
REPLY_KEY_PREFIX = "agent-reply"

# `(?<!@)` / `(?!\.[a-z])` so "send it to mom@gmail.com" is not an inbox read.
INBOX_TERMS = re.compile(
    r"(?<!@)\b(?:inbox|e-?mail|gmail|mail|unread|reply)\b(?!\.[a-z])",
    re.IGNORECASE,
)
GITHUB_TERMS = re.compile(
    r"\b(?:github|repo(?:sitory)?|pull request|pr|ci|commit|issue)\b", re.IGNORECASE
)


#: Questions about the calendar's past ("how many meetings last month"), which
#: are answered from precomputed rollups rather than live connector rows.
CALENDAR_HISTORY_TERMS = re.compile(
    r"\b(?:(?:last|past|previous)\s+(?:week|month|year|semester|quarter)|"
    r"history|historically|how often|how many|back in|earlier this (?:month|year))\b",
    re.IGNORECASE,
)
_CALENDAR_SUBJECT_TERMS = re.compile(
    r"\b(?:calendar|meetings?|events?|appointments?|schedule)\b", re.IGNORECASE
)


def wants_calendar_history(request: str) -> bool:
    return bool(CALENDAR_HISTORY_TERMS.search(request) and _CALENDAR_SUBJECT_TERMS.search(request))


def calendar_history_context(result: Any) -> dict[str, Any] | None:
    """Compress precomputed calendar history into a small model-facing pack.

    ``result`` is a calendar-history search result exposing ``groups`` and
    ``days`` (lists of dicts). Missing keys are tolerated: a thinner rollup
    must shrink the pack rather than fail the turn.
    """
    groups_in = list(getattr(result, "groups", None) or [])
    days_in = list(getattr(result, "days", None) or [])
    if not groups_in and not days_in:
        return None
    groups = [
        {
            "label": group.get("label"),
            "first_day": group.get("first_day"),
            "last_day": group.get("last_day"),
            "item_count": (group.get("stats") or {}).get("items"),
            "types": (group.get("stats") or {}).get("types"),
        }
        for group in groups_in
    ]
    items: list[dict[str, Any]] = []
    for day in days_in:
        for item in day.get("items") or []:
            items.append(
                {
                    "day": day.get("day"),
                    "group": day.get("label"),
                    "title": item.get("title"),
                    "type": item.get("item_type"),
                    "status": item.get("status"),
                    "at": item.get("at"),
                    "added_by": item.get("added_by"),
                    "organizer": item.get("organizer"),
                }
            )
            if len(items) >= 10:
                break
        if len(items) >= 10:
            break
    return {
        "groups": groups,
        "relevant_items": items,
        "scope": "derived from immutable local Calendar events; full evidence remains local",
    }


def fit_context_budget(context: dict[str, Any], limit: int) -> dict[str, Any]:
    """Drop lowest-ranked context items until the serialized pack fits.

    Connector builders rank useful items first, so trimming from list tails is
    deterministic and cheap. ``recent_conversation`` is ordered oldest first
    instead, so it is trimmed from the head -- an over-budget conversation
    should lose its earliest exchange, not the one the current message is
    actually replying to. Conversation is trimmed before current connector
    facts; the current request itself never enters this function.
    """
    if limit < 256:
        raise ValueError("context_char_budget must be at least 256")
    compact = json.loads(json.dumps(context))

    def size() -> int:
        return len(json.dumps(compact, ensure_ascii=False, separators=(",", ":")))

    paths = (
        ("recent_conversation",),
        ("github", "notifications"),
        ("calendar_history", "relevant_items"),
        ("gmail", "relevant"),
        ("memory", "relationships"),
        ("memory", "entities"),
        ("memory", "memories"),
        ("calendar_history", "groups"),
    )
    while size() > limit:
        changed = False
        for path in paths:
            target: Any = compact
            for key in path[:-1]:
                if not isinstance(target, dict):
                    target = None
                    break
                target = target.get(key)
            values = target.get(path[-1]) if isinstance(target, dict) else None
            if isinstance(values, list) and values:
                values.pop(0 if path == ("recent_conversation",) else -1)
                changed = True
                if size() <= limit:
                    return compact
        if not changed:
            break
    return compact


def serialize_context(context: dict[str, Any]) -> str:
    """Serialize a pack for embedding in a prompt, with angle brackets escaped.

    Escaping ``<`` and ``>`` means a malicious synced subject or snippet
    cannot manufacture a closing context tag. JSON unicode escapes preserve
    the text the model sees while keeping the delimiter structurally unique.
    """
    packed = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    return packed.replace("<", "\\u003c").replace(">", "\\u003e")


def topic_text_for_tools(request: str, history: list[dict[str, str]]) -> str:
    """Text that decides which tool group a turn is offered.

    Only a request too short to stand on its own inherits the previous
    exchange. Scanning all of history created a feedback loop: the assistant's
    own reply "i handle the boring stuff like email, github, tasks, calendar"
    put four topic words into the pool, so the next question -- about a tennis
    tournament -- was handed calendar tools and spent its budget calling them.
    A model's description of itself is not evidence about what the user wants.

    The prior assistant turn still matters for a bare "yeah do that", where
    the proposal being confirmed is the only place the topic exists at all.
    """
    if len(request.split()) > FOLLOW_UP_MAX_WORDS:
        return request
    recent = history[-1:]
    return "\n".join(
        [
            request,
            *(str(exchange["user"]) for exchange in recent),
            *(str(exchange["assistant"]) for exchange in recent),
        ]
    )


def recent_conversation(
    database: Database,
    *,
    chat_id: int,
    exclude_external_id: str,
    casual: bool = False,
    source: str = "telegram",
    reply_key_prefix: str = REPLY_KEY_PREFIX,
) -> list[dict[str, str]]:
    """Return recent answered exchanges for one chat, oldest first."""
    lookback = CASUAL_CONVERSATION_LOOKBACK_SECONDS if casual else CONVERSATION_LOOKBACK_SECONDS
    max_exchanges = CASUAL_MAX_CONTEXT_EXCHANGES if casual else MAX_CONTEXT_EXCHANGES
    cutoff = (datetime.now(UTC) - timedelta(seconds=lookback)).isoformat()
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT external_id, content, metadata_json, occurred_at
            FROM events
            WHERE source = ? AND occurred_at >= ? AND external_id != ?
            ORDER BY occurred_at DESC
            LIMIT 80
            """,
            (source, cutoff, exclude_external_id),
        ).fetchall()
        exchanges: list[dict[str, str]] = []
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            if metadata.get("chat_id") != chat_id or not metadata.get("agent_deferred"):
                continue
            # Insertion order, not key order: the bubble index is decimal
            # inside the key, so a lexicographic tie-break puts bubble 10
            # ahead of bubble 2 and reassembles the answer out of order.
            reply_rows = connection.execute(
                """
                SELECT idempotency_key, payload_json FROM outbox
                WHERE idempotency_key LIKE ?
                ORDER BY created_at, rowid
                """,
                (f"{reply_key_prefix}:{row['external_id']}:%",),
            ).fetchall()
            if not reply_rows:
                continue
            assistant = "\n\n".join(
                str(json.loads(reply["payload_json"]).get("text", "")) for reply in reply_rows
            ).strip()
            if assistant:
                exchanges.append({"user": str(row["content"] or ""), "assistant": assistant})
            if len(exchanges) >= max_exchanges:
                break
    exchanges.reverse()
    return exchanges


def connector_records(
    database: Database, connector: str, record_type: str, *, limit: int
) -> tuple[list[dict[str, Any]], str | None, int]:
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT record_id, payload_json, observed_at FROM connector_records
            WHERE connector = ? AND record_type = ? AND active = 1
            ORDER BY observed_at DESC
            LIMIT ?
            """,
            (connector, record_type, limit),
        ).fetchall()
        state = connection.execute(
            """
            SELECT last_success_at FROM sync_state
            WHERE connector = ? AND account = 'self'
            """,
            (connector,),
        ).fetchone()
        count = connection.execute(
            """
            SELECT COUNT(*) AS total FROM connector_records
            WHERE connector = ? AND record_type = ? AND active = 1
            """,
            (connector, record_type),
        ).fetchone()
    return (
        [
            {
                "record_id": str(row["record_id"]),
                "payload": json.loads(row["payload_json"]),
                "observed_at": str(row["observed_at"]),
            }
            for row in rows
        ],
        str(state["last_success_at"]) if state and state["last_success_at"] else None,
        int(count["total"]),
    )


def gmail_context(
    database: Database, *, trace_candidates: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    records, freshness, total = connector_records(database, "gmail", "unread_message", limit=50)
    relevant: list[dict[str, Any]] = []
    low_priority = 0
    candidates: list[dict[str, Any]] = []
    for record in records:
        payload = record["payload"]
        if low_priority_mail(payload):
            low_priority += 1
            continue
        candidates.append(record)
    scores = ResponseFeedbackService(database).scores(
        source="gmail", record_ids={str(record["record_id"]) for record in candidates}
    )
    candidates.sort(
        key=lambda record: (
            mail_rank(record["payload"])[0],
            mail_rank(record["payload"])[1],
            -scores.get(str(record["record_id"]), 0),
            mail_rank(record["payload"])[2],
        )
    )
    selected = candidates[:8]
    if trace_candidates is not None:
        trace_candidates["gmail"] = [str(record["record_id"]) for record in selected]
    for record in selected:
        payload = record["payload"]
        relevant.append(
            {
                key: payload.get(key)
                for key in ("subject", "from", "snippet", "html_url")
                if payload.get(key) is not None
            }
        )
    return {
        "freshness": freshness,
        "total_unread": total,
        "relevant": relevant,
        "low_priority_omitted": low_priority,
        "other_omitted": max(0, total - low_priority - len(relevant)),
        "scope": "currently active unread messages captured by the latest bounded sync",
        "content_limit": "headers and snippets only; full message bodies are not synced",
    }


def github_context(
    database: Database, *, trace_candidates: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    records, freshness, total = connector_records(database, "github", "notification", limit=20)
    scores = ResponseFeedbackService(database).scores(
        source="github", record_ids={str(record["record_id"]) for record in records}
    )
    ranked = [
        record
        for _, record in sorted(
            enumerate(records),
            key=lambda pair: (-scores.get(str(pair[1]["record_id"]), 0), pair[0]),
        )
    ]
    selected = ranked[:10]
    if trace_candidates is not None:
        trace_candidates["github"] = [str(record["record_id"]) for record in selected]
    notifications = [
        {
            key: record["payload"].get(key)
            for key in ("title", "repo", "reason", "subject_type", "html_url")
            if record["payload"].get(key) is not None
        }
        for record in selected
    ]
    return {
        "freshness": freshness,
        "total_unread": total,
        "notifications": notifications,
        "omitted": max(0, total - len(notifications)),
        "scope": "currently active unread notifications captured by the latest sync",
    }


def memory_context(
    memory_graph: MemoryGraph, request: str, *, include_vectors: bool = True
) -> dict[str, Any] | None:
    recalled = memory_graph.search(
        request,
        limit=5,
        allowed_sensitivities={"public", "personal"},
        include_vectors=include_vectors,
    )
    if not recalled.memories and not recalled.entities and not recalled.relationships:
        return None
    return {
        "memories": [
            {"id": item.id, "kind": item.kind, "statement": item.statement}
            for item in recalled.memories
        ],
        "entities": [
            {"id": item.id, "type": item.entity_type, "label": item.label}
            for item in recalled.entities
        ],
        "relationships": [
            {
                "id": item.id,
                "source": item.source_entity_id,
                "predicate": item.predicate,
                "target": item.target_entity_id,
            }
            for item in recalled.relationships
        ],
        "instruction": (
            "Use only when relevant. If the user corrects a recalled memory, call "
            "memory_correct with its id. Record explicit relevance feedback when clear."
        ),
    }


def build_trace(
    context: dict[str, Any], trace_candidates: dict[str, list[str]]
) -> dict[str, Any]:
    """Summarize which sources and records a pack actually included."""
    freshness: dict[str, str | None] = {}
    items: list[dict[str, str | int]] = []
    for source in ("gmail", "github"):
        source_context = context.get(source)
        if not isinstance(source_context, dict):
            continue
        freshness[source] = source_context.get("freshness")
        list_key = "relevant" if source == "gmail" else "notifications"
        included = source_context.get(list_key)
        included_count = len(included) if isinstance(included, list) else 0
        for rank, record_id in enumerate(trace_candidates.get(source, [])[:included_count]):
            items.append({"source": source, "record_id": record_id, "rank": rank})
    memory = context.get("memory")
    if isinstance(memory, dict):
        for rank, item in enumerate(memory.get("memories", [])):
            if isinstance(item, dict) and item.get("id"):
                items.append({"source": "memory", "record_id": str(item["id"]), "rank": rank})
    return {"sources": list(context), "freshness": freshness, "items": items}
