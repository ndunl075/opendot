"""Assemble the running agent: loop, rules, meter, reviewer, approval execution and the API app.

This is the one place the production pieces are wired together, so the daemon (M3 task 3.5
serves the returned app on 127.0.0.1) and the tests build exactly the same thing:

    user approves over HTTP -> ApprovalService issues a one-time token -> TokenEscrow
    -> AgentLoop resumes the task -> ApprovalGatedTools.run_approved -> ApprovalExecutor
    -> ActionExecutor (the connector's idempotent safe path)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from starlette.applications import Starlette

from ..api.escrow import TokenEscrow
from ..api.server import create_app
from ..db import Database
from ..policy import ApprovalService
from ..providers.errors import ProviderError
from ..providers.registry import DEFAULT_PROVIDER, ProviderRegistry
from ..router import Router
from ..rules import RuleEngine
from ..usage.meter import Budgets, UsageMeter
from .executor import ApprovalExecutor, ApprovalGatedTools, ApprovedHandler
from .loop import AgentLoop
from .packer import PromptPacker
from .reviewer import Reviewer


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class AgentRuntime:
    database: Database
    loop: AgentLoop
    app: Starlette
    escrow: TokenEscrow
    approvals: ApprovalService
    rules: RuleEngine
    meter: UsageMeter
    router: Router
    reviewer: Reviewer
    tools: ApprovalGatedTools


def build_agent_runtime(
    database: Database,
    registry: ProviderRegistry,
    tools: Any,
    *,
    api_token: str,
    actor: str = "owner",
    provider_name: str = DEFAULT_PROVIDER,
    reviewer_provider: str | None = None,
    budgets: Budgets | None = None,
    execute: ApprovedHandler | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> AgentRuntime:
    """Build the whole agent. ``tools`` supplies specs, intents, auto runs and proposals;
    approved actions always execute through the escrow and ``execute`` (default ``ActionExecutor``).
    The model catalog is read from the chosen provider (tiers are discovered, never hard-coded)."""
    database.migrate()
    approvals = ApprovalService(database)
    escrow = TokenEscrow()
    gated = ApprovalGatedTools(tools, ApprovalExecutor(approvals, escrow, actor=actor, execute=execute))
    try:
        catalog = registry.get(provider_name).list_models()
    except ProviderError:
        catalog = []  # not signed in yet: the loop re-reads the catalog on its first request
    router = Router(catalog)
    meter = UsageMeter(database, budgets=budgets, clock=clock)
    rules = RuleEngine(database)
    reviewer = Reviewer(
        database, registry, router, meter, provider_name=reviewer_provider or provider_name, clock=clock
    )
    loop = AgentLoop(
        database, registry, router, meter, rules, PromptPacker(), gated, reviewer,
        provider_name=provider_name, clock=clock,
    )
    app = create_app(
        loop=loop, approvals=approvals, rules=rules, token=api_token, token_escrow=escrow, meter=meter, actor=actor,
        database=database, registry=registry, extras={"router": router, "reviewer": reviewer},
    )
    return AgentRuntime(
        database=database, loop=loop, app=app, escrow=escrow, approvals=approvals, rules=rules, meter=meter,
        router=router, reviewer=reviewer, tools=gated,
    )


__all__ = ["AgentRuntime", "build_agent_runtime"]
