"""Credit math and the persisted usage meter."""

from __future__ import annotations

import re
import tomllib
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from ..db import Database
from ..providers.types import Usage
from .errors import DailyBudgetExceeded, TaskBudgetExceeded

DEFAULT_TASK_CREDITS = 5.0
DEFAULT_DAILY_CREDITS = 50.0
TOKENS_PER_UNIT = 1_000_000


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FamilyRate(BaseModel):
    input: float
    cached_input: float
    output: float


class CreditCost(BaseModel):
    credits: float
    rate_known: bool


class RateCard:
    def __init__(self, families: dict[str, FamilyRate], unknown_family: str) -> None:
        self.families = {name.lower(): rate for name, rate in families.items()}
        self.unknown_family = unknown_family.lower()
        if self.unknown_family not in self.families:
            raise ValueError(f"unknown_family {unknown_family!r} is not in the rate card")

    def family_for(self, model: str) -> str | None:
        lowered = model.lower()
        for family in self.families:
            if re.search(rf"(?<![a-z0-9]){re.escape(family)}(?![a-z0-9])", lowered):
                return family
        return None

    def credits(self, model: str, usage: Usage) -> CreditCost:
        """Input tokens include the cached ones; cached input is priced at the cached rate."""
        family = self.family_for(model)
        rate = self.families[family or self.unknown_family]
        input_tokens = max(usage.input_tokens, 0)
        cached = min(max(usage.cached_input_tokens, 0), input_tokens)
        fresh = input_tokens - cached
        total = fresh * rate.input + cached * rate.cached_input + max(usage.output_tokens, 0) * rate.output
        return CreditCost(credits=total / TOKENS_PER_UNIT, rate_known=family is not None)


def load_rate_card(path: Path | str | None = None) -> RateCard:
    if path is None:
        raw = files("opendot_core.usage").joinpath("rate_card.toml").read_text(encoding="utf-8")
    else:
        raw = Path(path).read_text(encoding="utf-8")
    data = tomllib.loads(raw)
    families = {name: FamilyRate(**values) for name, values in data["families"].items()}
    return RateCard(families, data.get("unknown_family", "sol"))


class Budgets(BaseModel):
    task_credits: float = DEFAULT_TASK_CREDITS
    daily_credits: float = DEFAULT_DAILY_CREDITS


class CreditRecord(BaseModel):
    id: int
    task_id: str
    job_type: str
    model: str
    effort: str | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    credits: float
    rate_known: bool
    day: str
    created_at: datetime
    step: int = 0
    """1-based position of this call within its task."""


class UsageLine(BaseModel):
    key: str
    credits: float
    calls: int


class UsageSummary(BaseModel):
    days: int
    since: str
    today: str
    total_credits: float
    credits_today: float
    budgets: Budgets
    by_task: list[UsageLine]
    by_day: list[UsageLine]
    by_job_type: list[UsageLine]
    unknown_rate_calls: int = 0


class UsageMeter:
    def __init__(
        self,
        database: Database,
        *,
        budgets: Budgets | None = None,
        clock: Callable[[], datetime] = _utc_now,
        rate_card: RateCard | None = None,
    ) -> None:
        self.database = database
        self.clock = clock
        self.rate_card = rate_card or load_rate_card()
        # Explicit budgets seed a fresh database; stored (user-changed) budgets always win.
        if budgets is not None and self._stored_budgets() is None:
            self.set_budgets(task_credits=budgets.task_credits, daily_credits=budgets.daily_credits)

    # -- time ---------------------------------------------------------------

    def _now(self) -> datetime:
        now = self.clock()
        return now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)

    def today(self) -> str:
        return self._now().date().isoformat()

    # -- budgets ------------------------------------------------------------

    def _stored_budgets(self) -> Budgets | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT task_credits, daily_credits FROM usage_budgets WHERE id = 1").fetchone()
        return Budgets(task_credits=row["task_credits"], daily_credits=row["daily_credits"]) if row else None

    def get_budgets(self) -> Budgets:
        return self._stored_budgets() or Budgets()

    def set_budgets(self, *, task_credits: float | None = None, daily_credits: float | None = None) -> Budgets:
        current = self.get_budgets()
        new = Budgets(
            task_credits=current.task_credits if task_credits is None else task_credits,
            daily_credits=current.daily_credits if daily_credits is None else daily_credits,
        )
        if new.task_credits < 0 or new.daily_credits < 0:
            raise ValueError("budgets cannot be negative")
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO usage_budgets (id, task_credits, daily_credits, updated_at) VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET task_credits = excluded.task_credits, "
                "daily_credits = excluded.daily_credits, updated_at = excluded.updated_at",
                (new.task_credits, new.daily_credits, self._now().isoformat()),
            )
            connection.commit()
        return new

    # -- recording ----------------------------------------------------------

    def record(
        self, *, task_id: str, job_type: str, model: str, effort: str | None, usage: Usage
    ) -> CreditRecord:
        cost = self.rate_card.credits(model, usage)
        now = self._now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO credit_records (task_id, job_type, model, effort, input_tokens, cached_input_tokens, "
                "output_tokens, reasoning_tokens, credits, rate_known, day, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    job_type,
                    model,
                    effort,
                    usage.input_tokens,
                    usage.cached_input_tokens,
                    usage.output_tokens,
                    usage.reasoning_tokens,
                    cost.credits,
                    int(cost.rate_known),
                    now.date().isoformat(),
                    now.isoformat(),
                ),
            )
            record_id = int(cursor.lastrowid)
            step = connection.execute(
                "SELECT COUNT(*) FROM credit_records WHERE task_id = ? AND id <= ?", (task_id, record_id)
            ).fetchone()[0]
            connection.commit()
        return CreditRecord(
            id=record_id,
            task_id=task_id,
            job_type=job_type,
            model=model,
            effort=effort,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            credits=cost.credits,
            rate_known=cost.rate_known,
            day=now.date().isoformat(),
            created_at=now,
            step=step,
        )

    def records_for_task(self, task_id: str) -> list[CreditRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM credit_records WHERE task_id = ? ORDER BY id", (task_id,)
            ).fetchall()
        return [self._row_to_record(row, step) for step, row in enumerate(rows, start=1)]

    @staticmethod
    def _row_to_record(row: Any, step: int) -> CreditRecord:
        data = dict(row)
        data["rate_known"] = bool(data["rate_known"])
        return CreditRecord(**data, step=step)

    # -- totals -------------------------------------------------------------

    def credits_for_task(self, task_id: str) -> float:
        with self.database.connect() as connection:
            return float(
                connection.execute(
                    "SELECT COALESCE(SUM(credits), 0) FROM credit_records WHERE task_id = ?", (task_id,)
                ).fetchone()[0]
            )

    def credits_today(self) -> float:
        with self.database.connect() as connection:
            return float(
                connection.execute(
                    "SELECT COALESCE(SUM(credits), 0) FROM credit_records WHERE day = ?", (self.today(),)
                ).fetchone()[0]
            )

    def summary(self, days: int = 7) -> UsageSummary:
        today = self._now().date()
        since = (today - timedelta(days=max(days, 1) - 1)).isoformat()
        with self.database.connect() as connection:

            def grouped(column: str, order: str) -> list[UsageLine]:
                rows = connection.execute(
                    f"SELECT {column} AS key, SUM(credits) AS credits, COUNT(*) AS calls FROM credit_records "
                    f"WHERE day >= ? AND day <= ? GROUP BY {column} ORDER BY {order}",
                    (since, today.isoformat()),
                ).fetchall()
                return [UsageLine(key=row["key"], credits=row["credits"], calls=row["calls"]) for row in rows]

            by_task = grouped("task_id", "credits DESC, key")
            by_day = grouped("day", "key")
            by_job_type = grouped("job_type", "credits DESC, key")
            unknown = connection.execute(
                "SELECT COUNT(*) FROM credit_records WHERE rate_known = 0 AND day >= ? AND day <= ?",
                (since, today.isoformat()),
            ).fetchone()[0]
        return UsageSummary(
            days=days,
            since=since,
            today=today.isoformat(),
            total_credits=sum(line.credits for line in by_day),
            credits_today=self.credits_today(),
            budgets=self.get_budgets(),
            by_task=by_task,
            by_day=by_day,
            by_job_type=by_job_type,
            unknown_rate_calls=int(unknown),
        )

    # -- budget checks ------------------------------------------------------

    def task_overrun_allowed(self, task_id: str) -> bool:
        with self.database.connect() as connection:
            return (
                connection.execute("SELECT 1 FROM task_budget_overruns WHERE task_id = ?", (task_id,)).fetchone()
                is not None
            )

    def allow_task_overrun(self, task_id: str) -> None:
        """Record the user's "continue anyway" for one task. The daily budget still applies."""
        with self.database.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO task_budget_overruns (task_id, allowed_at) VALUES (?, ?)",
                (task_id, self._now().isoformat()),
            )
            connection.commit()

    def check_before_request(self, task_id: str) -> None:
        budgets = self.get_budgets()
        spent_today = self.credits_today()
        if spent_today >= budgets.daily_credits:
            raise DailyBudgetExceeded(
                f"Daily budget reached ({spent_today:.2f} of {budgets.daily_credits:g} credits). "
                "Plan requests are paused until tomorrow (UTC) or until you raise the daily budget.",
                spent=spent_today,
                limit=budgets.daily_credits,
            )
        spent_task = self.credits_for_task(task_id)
        if spent_task >= budgets.task_credits and not self.task_overrun_allowed(task_id):
            raise TaskBudgetExceeded(
                f"This task has used {spent_task:.2f} of its {budgets.task_credits:g} credit budget. "
                "Paused: choose continue anyway to keep going.",
                spent=spent_task,
                limit=budgets.task_credits,
                task_id=task_id,
            )
