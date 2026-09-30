"""Budget errors raised by the usage meter before a plan request is sent."""

from __future__ import annotations


class BudgetExceeded(Exception):
    """A credit budget has been reached; the request must not be sent."""

    def __init__(self, message: str, *, spent: float, limit: float, task_id: str | None = None) -> None:
        super().__init__(message)
        self.spent = spent
        self.limit = limit
        self.task_id = task_id

    @property
    def user_message(self) -> str:
        return str(self)


class DailyBudgetExceeded(BudgetExceeded):
    """Hard stop: no override except raising the daily budget or waiting for the next UTC day."""


class TaskBudgetExceeded(BudgetExceeded):
    """The task pauses; the user may choose "continue anyway" (``UsageMeter.allow_task_overrun``)."""
