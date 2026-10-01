"""``GET /v1/activity``: what the companion did and why.

Built from the hash-chained audit log (``tool_runs``) and the agent loop's tasks and steps. Rule
decisions link to the rule that applied, to the reviewer's note on the approval, and to the memories
the task looked at. The cursor is the position of the last item returned, newest first.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ..models import API_VERSION, ActivityItem, ActivityList, ActivityQuery
from ._common import error, invalid, parse_time, read_query, reply

if TYPE_CHECKING:
    from . import ApiContext

_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_MEMORY_TOOLS = frozenset({"memory_search", "profile_get", "memory_correct", "remember", "forget", "memory_feedback"})
_MEMORY_AUDIT = {
    "memory_create": "Remembered something",
    "memory_supersede": "Corrected a memory",
    "memory_forget": "Forgot a memory",
    "memory_forget_by_source_event": "Forgot memories from one source",
    "memory_feedback": "Recorded feedback on a memory",
}
_APPROVAL_AUDIT = {
    "approval_propose": "Asked for your approval",
    "approval_approve": "You approved an action",
    "approval_reject": "You declined an action",
    "approval_consume": "Carried out an approved action",
}
_BEHAVIOR_TITLE = {
    "auto": "Used {tool}",
    "auto_if_preapproved": "Used {tool}, as you asked",
    "ask": "Asked before using {tool}",
    "handoff": "Declined to use {tool}; left it to you",
}


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _words(tool: str) -> str:
    return tool.replace("_", " ").replace(".", " ").strip().capitalize() or "Action"


def _json(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


class _Resolver:
    """Looks up the rule, reviewer note, approval and memory ids behind one task's decisions."""

    def __init__(self, ctx: Any, connection: Any) -> None:
        self.connection = connection
        self.rules = {rule.id: rule for rule in ctx.rules.list_rules()}
        self._steps: dict[str, list[Any]] = {}

    def rule_name(self, rule_id: str | None) -> str | None:
        rule = self.rules.get(rule_id or "")
        if rule is None:
            return None
        return rule.note or f"{rule.tool} ({rule.action})"

    def steps(self, task_id: str) -> list[Any]:
        if task_id not in self._steps:
            self._steps[task_id] = self.connection.execute(
                "SELECT tool, approval_id, output_json FROM agent_steps WHERE task_id = ? AND kind = 'tool' ORDER BY id",
                (task_id,),
            ).fetchall()
        return self._steps[task_id]

    def for_decision(
        self, task_id: str | None, acted: str, behavior: str, sequence: int
    ) -> tuple[str | None, str | None, list[str]]:
        """(approval id, reviewer note, memory ids) behind one rule decision about ``acted`` in a task.

        An ``ask`` decision pairs with the task's approval steps in order (the Nth ask decision of a
        task is the Nth waiting step); a memory tool's decision lists the memories the task looked at.
        """
        if not task_id:
            return None, None, []
        approval_id: str | None = None
        note: str | None = None
        memory_ids: list[str] = []
        steps = self.steps(task_id)
        if behavior == "ask":
            ordinal = self.connection.execute(
                "SELECT COUNT(*) FROM tool_runs WHERE tool = 'rule_decision' AND outcome = 'ask' "
                "AND correlation_id = ? AND sequence < ?",
                (task_id, sequence),
            ).fetchone()[0]
            waiting = [step for step in steps if step["approval_id"]]
            if ordinal < len(waiting):
                approval_id = waiting[ordinal]["approval_id"]
                review = _json(waiting[ordinal]["output_json"]).get("review")
                if isinstance(review, dict):
                    reasons = review.get("reasons") or []
                    note = "; ".join(str(r) for r in reasons) or "No concerns found."
        if acted in _MEMORY_TOOLS:
            for step in steps:
                if step["tool"] in _MEMORY_TOOLS:
                    for found in _UUID.findall(str(_json(step["output_json"]).get("result", ""))):
                        if found not in memory_ids:
                            memory_ids.append(found)
        return approval_id, note, memory_ids


def _audit_item(row: Any, resolver: _Resolver) -> ActivityItem:
    tool = row["tool"]
    result = _json(row["result_json"])
    at = parse_time(row["occurred_at"])
    base: dict[str, Any] = {"id": f"audit:{row['id']}", "at": at}
    if tool == "rule_decision":
        behavior = str(result.get("behavior", row["outcome"]))
        acted = str(result.get("tool", "a tool"))
        rule_id = result.get("rule_id")
        approval_id, note, memory_ids = resolver.for_decision(row["correlation_id"], acted, behavior, row["sequence"])
        return ActivityItem(
            **base,
            kind="approval" if behavior == "ask" else "action",
            title=_BEHAVIOR_TITLE.get(behavior, "Considered {tool}").format(tool=acted.replace("_", " ")),
            why=str(result.get("reason") or "A rule decided this."),
            rule_id=rule_id,
            rule_name=resolver.rule_name(rule_id),
            reviewer_note=note,
            memory_ids=memory_ids,
            approval_id=approval_id,
        )
    if tool in _APPROVAL_AUDIT:
        return ActivityItem(
            **base, kind="approval", title=_APPROVAL_AUDIT[tool],
            why=f"Action: {_words(str(result.get('action_type', 'an action')))}." if "action_type" in result
            else "A decision on a proposed action.",
            approval_id=result.get("approval_id"),
        )
    if tool in _MEMORY_AUDIT:
        ids = [str(result[k]) for k in ("memory_id", "new_memory_id", "old_memory_id") if result.get(k)]
        return ActivityItem(
            **base, kind="action", title=_MEMORY_AUDIT[tool],
            why=str(result.get("reason") or f"By {row['actor']}."), memory_ids=ids,
        )
    if tool.endswith("_sync") or "_sync" in tool:
        return ActivityItem(**base, kind="sync", title=f"{_words(tool.removesuffix('_read_sync'))} synced",
                            why="A scheduled read-only sync.")
    if "reminder" in tool or tool.startswith("scheduled_task"):
        return ActivityItem(**base, kind="reminder", title=_words(tool), why=f"By {row['actor']}.")
    if "pause" in tool or tool in ("kill_switch", "companion_reset"):
        return ActivityItem(**base, kind="pause", title=_words(tool), why=f"By {row['actor']}.")
    if tool.startswith("rule_") or tool == "client_scope_grant":
        return ActivityItem(**base, kind="rule_change", title=_words(tool), why=f"By {row['actor']}.")
    return ActivityItem(**base, kind="action", title=_words(tool), why=f"Recorded by {row['client']} ({row['outcome']}).")


def _task_item(row: Any, ctx: Any) -> ActivityItem:
    meter = ctx.meter
    message = " ".join(str(row["message"]).split())
    state = str(row["state"]).replace("_", " ")
    return ActivityItem(
        id=f"task:{row['id']}",
        at=parse_time(row["created_at"]),  # type: ignore[arg-type]
        kind="message",
        title="You asked: " + (message if len(message) <= 80 else message[:79].rstrip() + "…"),
        why=f"Handled as a {row['job_type']} request; now {state}.",
        credits=float(meter.credits_for_task(row["id"])) if meter is not None else 0.0,
    )


def build_page(ctx: Any, query: ActivityQuery) -> ActivityList:
    since = _iso(query.since) if query.since else None
    after: tuple[str, str] | None = None
    if query.cursor:
        stamp, _, item_id = query.cursor.partition("|")
        after = (stamp, item_id)
    items: list[tuple[str, str, ActivityItem]] = []
    with ctx.database.connect() as connection:
        resolver = _Resolver(ctx, connection)
        for table, builder, column, id_prefix in (
            ("tool_runs", lambda r: _audit_item(r, resolver), "occurred_at", "audit:"),
            ("agent_tasks", lambda r: _task_item(r, ctx), "created_at", "task:"),
        ):
            clauses, params = [], []
            if since:
                clauses.append(f"{column} >= ?")
                params.append(since)
            if after:
                clauses.append(f"({column} < ? OR ({column} = ? AND ? || id < ?))")
                params.extend([after[0], after[0], id_prefix, after[1]])
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = connection.execute(
                f"SELECT * FROM {table} {where} ORDER BY {column} DESC, id DESC LIMIT ?", [*params, query.limit + 1]
            ).fetchall()
            for row in rows:
                item = builder(row)
                items.append((row[column], item.id, item))
    items.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    page = items[: query.limit]
    next_cursor = f"{page[-1][0]}|{page[-1][1]}" if len(items) > query.limit and page else None
    return ActivityList(items=[entry[2] for entry in page], next_cursor=next_cursor)


def routes(ctx: ApiContext) -> list[Route]:
    if ctx.database is None or ctx.rules is None:
        return []

    async def activity(request: Request) -> Response:
        try:
            query: ActivityQuery = read_query(request, ActivityQuery)
        except ValidationError as problem:
            return invalid(problem)
        try:
            page = await asyncio.to_thread(build_page, ctx, query)
        except ValueError:
            return error(422, "invalid_request", "That cursor is not valid.")
        return reply(page)

    return [Route(f"/{API_VERSION}/activity", activity, methods=["GET"])]
