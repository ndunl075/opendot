"""Builds the agent loop a routine's model pass runs through (the production wiring, without the HTTP app).

The loop has no tools. The ChatGPT plan provider is the only provider registered; opt-in providers stay
off unless the user enabled them elsewhere, exactly as for chat.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..agent.loop import AgentLoop
from ..agent.packer import PromptPacker
from ..agent.reviewer import Reviewer
from ..db import Database
from ..providers.errors import ProviderError
from ..providers.registry import DEFAULT_PROVIDER, ProviderRegistry, ProviderSettings
from ..router import Router
from ..rules import RuleEngine
from ..usage.meter import UsageMeter


class NoTools:
    """A routine's tool layer: nothing. Reading is done by code before the model sees anything."""

    actor = "system:routines"

    def specs(self) -> list[Any]:
        return []

    def intent(self, name: str, arguments: Any) -> Any:
        raise KeyError(name)

    def run(self, name: str, arguments: Any, *, task_id: str, call_id: str) -> str:
        raise KeyError(name)

    def propose(self, name: str, arguments: Any, *, task_id: str) -> Any:
        raise KeyError(name)

    def run_approved(self, approval_id: str) -> str:
        raise KeyError(approval_id)


def build_routine_loop(
    database: Database,
    registry: ProviderRegistry | None = None,
    *,
    provider_name: str = DEFAULT_PROVIDER,
    clock: Any = None,
) -> AgentLoop:
    database.migrate()
    if registry is None:
        from ..providers.chatgpt_plan import ChatGPTPlanProvider

        registry = ProviderRegistry(ProviderSettings())
        registry.register(ChatGPTPlanProvider())
    clock = clock or (lambda: datetime.now(UTC))
    try:
        catalog = registry.get(provider_name).list_models()
    except ProviderError:
        catalog = []  # not signed in yet: the loop re-reads the catalog on its first request
    router = Router(catalog)
    meter = UsageMeter(database, clock=clock)
    reviewer = Reviewer(database, registry, router, meter, provider_name=provider_name, clock=clock)
    return AgentLoop(
        database, registry, router, meter, RuleEngine(database), PromptPacker(), NoTools(), reviewer,
        provider_name=provider_name, clock=clock,
    )
