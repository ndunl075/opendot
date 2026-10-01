"""Usage endpoints (``usage_get``, ``usage_budgets_get``, ``usage_budgets_set``), backed by ``UsageMeter``.

Credits by task, day and job type come straight from the meter's records (ARCHITECTURE.md 8.3).
Budgets are the meter's stored budgets; the daily budget is a hard stop and stays one: a request to
turn the hard stop off is refused. A budget left out (null) keeps its current value.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ..models import UsageBudgets, UsageByDay, UsageByJobType, UsageByTask, UsageQuery, UsageSummary
from .config_support import error, ok, parse_body, run

if TYPE_CHECKING:
    from . import ApiContext

PLAN_LABEL = "ChatGPT plan"
MANAGE_USAGE_URL = "https://chatgpt.com/settings/usage"
_MAX_TASKS = 50


def routes(ctx: ApiContext) -> list[Route]:
    meter = ctx.meter
    if meter is None or ctx.database is None:
        return []

    def budgets_model() -> UsageBudgets:
        current = meter.get_budgets()
        return UsageBudgets(
            daily_credits=current.daily_credits, task_credits=current.task_credits, daily_hard_stop=True
        )

    def titles(task_ids: list[str]) -> dict[str, str]:
        if not task_ids:
            return {}
        marks = ",".join("?" for _ in task_ids)
        with ctx.database.connect() as connection:
            rows = connection.execute(f"SELECT id, message FROM agent_tasks WHERE id IN ({marks})", task_ids).fetchall()
        return {row["id"]: " ".join(str(row["message"]).split())[:80] for row in rows}

    def summary(days: int) -> UsageSummary:
        raw = meter.summary(days)
        credits_by_day = {line.key: line.credits for line in raw.by_day}
        first = date.fromisoformat(raw.since)
        by_day = [
            UsageByDay(day=(day := (first + timedelta(days=offset)).isoformat()), credits=credits_by_day.get(day, 0.0))
            for offset in range(days)
        ]
        tasks = raw.by_task[:_MAX_TASKS]
        known = titles([line.key for line in tasks])
        daily = raw.budgets.daily_credits
        return UsageSummary(
            days=days,
            total_credits=raw.total_credits,
            today_credits=raw.credits_today,
            by_day=by_day,
            by_task=[UsageByTask(task_id=t.key, title=known.get(t.key) or t.key, credits=t.credits) for t in tasks],
            by_job_type=[UsageByJobType(job_type=j.key, credits=j.credits) for j in raw.by_job_type],
            budgets=budgets_model(),
            daily_budget_remaining=max(daily - raw.credits_today, 0.0),
            plan_label=PLAN_LABEL,
            manage_usage_url=MANAGE_USAGE_URL,
            paused_for_budget=raw.credits_today >= daily,
        )

    async def usage_get(request: Request) -> Response:
        try:
            query = UsageQuery.model_validate(dict(request.query_params))
        except ValidationError as failure:
            return error(422, "invalid_request", str(failure.errors()[0]["msg"]))
        return ok(await run(summary, query.days))

    async def budgets_get(_: Request) -> Response:
        return ok(await run(budgets_model))

    async def budgets_set(request: Request) -> Response:
        body = await parse_body(request, UsageBudgets)
        if isinstance(body, Response):
            return body
        if not body.daily_hard_stop:
            return error(422, "hard_stop_required", "The daily budget is always a hard stop and cannot be turned off.")
        for value in (body.daily_credits, body.task_credits):
            if value is not None and value < 0:
                return error(422, "invalid_request", "Budgets cannot be negative.")

        def save() -> UsageBudgets:
            meter.set_budgets(task_credits=body.task_credits, daily_credits=body.daily_credits)
            return budgets_model()

        return ok(await run(save))

    v = "/v1/usage"
    return [
        Route(v, usage_get, methods=["GET"]),
        Route(f"{v}/budgets", budgets_get, methods=["GET"]),
        Route(f"{v}/budgets", budgets_set, methods=["PUT"]),
    ]
