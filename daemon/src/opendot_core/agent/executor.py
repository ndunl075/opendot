"""Execute approved proposals with the one-time token the user's approval put in the escrow.

The API server's approve endpoint issues the token and holds it in ``TokenEscrow``; the
agent loop calls ``run_approved`` on its tool executor, which delegates here. The token
never passes through the model, the loop's history or the UI. Tokens live only in memory:
after a restart ``run_approved`` raises ``PolicyError`` and the loop asks the user again
(or hands off if the approval was already consumed).

By default execution goes through ``ActionExecutor``, the same idempotent boundary MCP and
Telegram approvals use, so every connector's safe path is reused rather than duplicated.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from ..api.escrow import TokenEscrow
from ..policy import ApprovalService, PolicyError

#: ``execute(approval_id, *, actor, token) -> receipt`` (a model or a JSON-able dict).
#: Implementations must be idempotent by approval id, as ``ActionExecutor`` is.
ApprovedHandler = Callable[..., Any]


class ApprovalExecutor:
    def __init__(
        self,
        approvals: ApprovalService,
        escrow: TokenEscrow,
        *,
        actor: str,
        execute: ApprovedHandler | None = None,
        handlers: dict[str, ApprovedHandler] | None = None,
    ) -> None:
        """``handlers`` override ``execute`` per action type; ``execute`` defaults to ``ActionExecutor``."""
        self.approvals = approvals
        self.escrow = escrow
        self.actor = actor
        if execute is None:
            from ..action_executor import ActionExecutor

            execute = ActionExecutor(approvals.database).execute
        self.execute = execute
        self.handlers = dict(handlers or {})

    def run_approved(self, approval_id: str) -> str:
        approval = self.approvals.get(approval_id)
        if approval is None:
            raise PolicyError("approval does not exist")
        token = self.escrow.get(approval_id)
        if token is None:
            raise PolicyError("the one-time approval token is not available (for example after a restart)")
        handler = self.handlers.get(approval.action_type, self.execute)
        receipt = handler(approval_id, actor=self.actor, token=token)
        if isinstance(receipt, BaseModel):
            return receipt.model_dump_json()
        return json.dumps(receipt, sort_keys=True, default=str)


class ApprovalGatedTools:
    """A tool executor whose approved actions always run through an ``ApprovalExecutor``.

    Wraps the connector tools (specs, intents, auto runs and proposals) so no tool
    implementation can execute an approval by any other path.
    """

    def __init__(self, tools: Any, executor: ApprovalExecutor) -> None:
        self._tools = tools
        self.executor = executor
        self.actor = executor.actor

    def specs(self) -> Any:
        return self._tools.specs()

    def intent(self, name: str, arguments: Any) -> Any:
        return self._tools.intent(name, arguments)

    def run(self, name: str, arguments: Any, *, task_id: str, call_id: str) -> str:
        return self._tools.run(name, arguments, task_id=task_id, call_id=call_id)

    def propose(self, name: str, arguments: Any, *, task_id: str) -> Any:
        return self._tools.propose(name, arguments, task_id=task_id)

    def run_approved(self, approval_id: str) -> str:
        return self.executor.run_approved(approval_id)


__all__ = ["ApprovalExecutor", "ApprovalGatedTools", "ApprovedHandler"]
