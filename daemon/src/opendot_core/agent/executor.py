"""Execute approved proposals with the one-time token the user's approval put in the escrow.

The API server's approve endpoint issues the token and holds it in ``TokenEscrow``; the
agent loop calls ``run_approved`` on its tool executor, which delegates here. The token
never passes through the model, the loop's history or the UI. Tokens live only in memory:
after a restart ``run_approved`` raises ``PolicyError`` and the loop asks the user again
(or hands off if the approval was already consumed).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from ..api.escrow import TokenEscrow
from ..policy import ApprovalService, PolicyError

#: ``handler(approval_id, *, actor, token) -> receipt`` for one approval action type,
#: for example ``GmailActions(...).execute``. Handlers must be idempotent by approval id.
ApprovedHandler = Callable[..., BaseModel]


class ApprovalExecutor:
    def __init__(
        self,
        approvals: ApprovalService,
        escrow: TokenEscrow,
        *,
        actor: str,
        handlers: dict[str, ApprovedHandler],
    ) -> None:
        self.approvals = approvals
        self.escrow = escrow
        self.actor = actor
        self.handlers = dict(handlers)

    def run_approved(self, approval_id: str) -> str:
        approval = self.approvals.get(approval_id)
        if approval is None:
            raise PolicyError("approval does not exist")
        handler = self.handlers.get(approval.action_type)
        if handler is None:
            raise KeyError(f"no executor for {approval.action_type!r}")
        token = self.escrow.get(approval_id)
        if token is None:
            raise PolicyError("the one-time approval token is not available (for example after a restart)")
        receipt: Any = handler(approval_id, actor=self.actor, token=token)
        return receipt.model_dump_json()


__all__ = ["ApprovalExecutor", "ApprovedHandler"]
