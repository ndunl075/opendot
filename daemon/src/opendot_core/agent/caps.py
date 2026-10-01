"""Monthly call and dollar caps for paid model calls.

Both caps read the same audit rows (``tool_runs``), so a call cannot be billed
by one guard and invisible to the other. A call is recorded the moment it may
have cost money -- including failures and timeouts -- and never for a call
that provably did not start, so a launch failure cannot burn the allowance.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..audit import AuditEvent, AuditLog
from ..db import Database

#: The audit ``tool`` name under which each billable model call is recorded.
MODEL_CALL_TOOL = "agent_model_call"


class UsageCaps:
    """Enforce and record a monthly call limit and/or dollar budget."""

    def __init__(
        self,
        database: Database,
        *,
        monthly_call_limit: int | None = None,
        monthly_budget_usd: float | None = None,
        tool: str = MODEL_CALL_TOOL,
    ) -> None:
        self.database = database
        self.monthly_call_limit = monthly_call_limit
        self.monthly_budget_usd = monthly_budget_usd
        self.tool = tool

    def refusal(self) -> str | None:
        """Return why a new call must be refused, or None when it may proceed."""
        if self.monthly_call_limit is not None:
            if self.month_to_date_calls() >= self.monthly_call_limit:
                return "monthly call limit reached"
        # The dollar cap is the one that means anything. A call count assumes
        # every turn costs the same; turns ranged over an order of magnitude
        # in token count, so the count is only loosely related to the bill.
        if self.monthly_budget_usd is not None:
            spent = self.month_to_date_spend_usd()
            if spent >= self.monthly_budget_usd:
                return f"monthly budget reached: ${spent:.4f} of ${self.monthly_budget_usd:.2f}"
        return None

    def record_call(
        self,
        *,
        ok: bool,
        cost_usd: float | None = None,
        model: str | None = None,
        detail: str = "",
        duration_ms: int | None = None,
    ) -> None:
        """Audit one call that may have cost money, successful or not."""
        AuditLog(self.database).append(
            AuditEvent(
                actor="system:agent",
                client="agent",
                tool=self.tool,
                outcome="ok" if ok else "failed",
                result={
                    # The model name, never a credential.
                    "model": model,
                    "duration_ms": duration_ms,
                    "detail": detail or None,
                    "estimated_cost_usd": cost_usd,
                },
            )
        )

    def month_to_date_spend_usd(self) -> float:
        """Sum per-call cost estimates for the current UTC month.

        Rows written without a cost contribute nothing rather than raising,
        which degrades toward permitting -- the call cap still bounds those.
        """
        self.database.migrate()
        month = datetime.now(UTC).strftime("%Y-%m")
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT json_extract(result_json, '$.estimated_cost_usd') AS cost
                FROM tool_runs
                WHERE tool = ? AND occurred_at LIKE ?
                """,
                (self.tool, f"{month}%"),
            ).fetchall()
        return sum(float(row["cost"]) for row in rows if row["cost"] is not None)

    def month_to_date_calls(self) -> int:
        self.database.migrate()
        month = datetime.now(UTC).strftime("%Y-%m")
        with self.database.connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM tool_runs WHERE tool = ? AND occurred_at LIKE ?",
                    (self.tool, f"{month}%"),
                ).fetchone()[0]
            )
