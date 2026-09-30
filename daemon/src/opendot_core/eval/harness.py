"""Assemble a complete agent stack over a scripted provider, for the scenario suite.

``build_world`` builds only what needs no M2 module (database, scripted
provider, clock, fake outside services, the scenario tools). ``build_stack``
adds the M2 pieces (router, usage meter, rules, packer, reviewer, agent loop).
Those are imported lazily inside the function, so this module imports cleanly
before they exist; ``StackUnavailable`` then says which one is missing.

The names used here follow docs/m2-design.md:

- ``opendot_core.router.Router``
- ``opendot_core.usage.meter.UsageMeter`` and ``Budgets``
- ``opendot_core.rules.RuleEngine`` (plus ``Rule``, ``Behavior``, ``ToolIntent``)
- ``opendot_core.agent.packer.PromptPacker``
- ``opendot_core.agent.reviewer.Reviewer``
- ``opendot_core.agent.loop.AgentLoop``

Constructors are called with keyword arguments, and only the keywords a class
actually accepts are passed, so an extra optional parameter never breaks the
harness.
"""

from __future__ import annotations

import importlib
import inspect
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Sequence

from ..db import Database
from ..jobs import JobRunner
from ..policy import ApprovalService
from ..providers.registry import ProviderRegistry, ProviderSettings
from ..providers.types import ModelInfo, ToolSpec
from ..reminders import ReminderStore
from .fake_provider import ScriptedProvider, Turn

PLAN_PROVIDER = "chatgpt_plan"
START_TIME = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
ACTOR = "owner"
"""The identity that proposes and approves in scenarios (the approval service binds both to one actor)."""


class StackUnavailable(RuntimeError):
    """A module the suite needs has not been built yet."""

    def __init__(self, module: str, detail: str = "") -> None:
        self.module = module
        super().__init__(f"module {module} is not available" + (f": {detail}" if detail else ""))


class FakeClock:
    """An injectable clock. Calling it returns the current (fake) UTC time."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or START_TIME

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> datetime:
        self.now += timedelta(**kwargs)
        return self.now


class FakeGmail:
    """Stands in for Gmail's write API and records every call that would reach the outside world."""

    def __init__(self) -> None:
        self.create_calls: list[dict[str, str]] = []
        self.send_calls: list[dict[str, str]] = []
        self.deleted: list[str] = []
        self._drafts: dict[str, dict[str, str]] = {}

    def create_draft(self, *, message_id: str, to: str, subject: str, body: str) -> dict[str, Any]:
        self.create_calls.append({"message_id": message_id, "to": to, "subject": subject, "body": body})
        draft_id = f"draft-{len(self._drafts) + 1}"
        self._drafts[message_id] = {"id": draft_id}
        return {"id": draft_id}

    def find_draft_by_message_id(self, *, message_id: str) -> dict[str, Any] | None:
        return self._drafts.get(message_id)

    def send_message(self, *, message_id: str, to: str, subject: str, body: str) -> dict[str, Any]:
        self.send_calls.append({"message_id": message_id, "to": to})
        return {"id": f"sent-{len(self.send_calls)}"}

    def find_sent_message_by_message_id(self, *, message_id: str) -> dict[str, Any] | None:
        return None

    def delete_message(self, message_id: str) -> None:
        self.deleted.append(message_id)


@dataclass
class World:
    """Everything that lives outside the daemon process and so survives a restart."""

    db_path: Path
    provider: ScriptedProvider
    extra_providers: list[ScriptedProvider]
    clock: FakeClock
    gmail: FakeGmail = field(default_factory=FakeGmail)
    executions: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    """Every tool that actually ran (auto path or approved), in order."""
    tokens: dict[str, str] = field(default_factory=dict)
    """One-time approval tokens handed to the executor by ``Stack.approve``."""

    def executed(self, name: str) -> list[dict[str, Any]]:
        return [arguments for tool, arguments in self.executions if tool == name]


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="memory_search",
        description="Look up what is known about the user.",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    ),
    ToolSpec(
        name="reminder_set",
        description="Schedule a reminder.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}, "minutes": {"type": "number"}},
            "required": ["text", "minutes"],
        },
    ),
    ToolSpec(
        name="gmail_draft_create",
        description="Create a Gmail draft (needs approval).",
        parameters={
            "type": "object",
            "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
            "required": ["to", "subject", "body"],
        },
    ),
    ToolSpec(
        name="gmail_delete_message",
        description="Delete a Gmail message.",
        parameters={"type": "object", "properties": {"message_id": {"type": "string"}}, "required": ["message_id"]},
    ),
)


class ScenarioTools:
    """The tool executor the scenarios give the loop. Real stores, fake outside services.

    Interface the agent loop is expected to use:

    - ``specs() -> list[ToolSpec]``: every tool (the loop picks the per-task group).
    - ``intent(name, arguments) -> ToolIntent``: what the rule engine must decide on.
    - ``run(name, arguments, *, task_id, call_id) -> str``: the ``auto`` path; returns the tool result text.
    - ``propose(name, arguments, *, task_id) -> Approval``: the ``ask`` path; creates the approval, runs nothing.
    - ``run_approved(approval_id) -> str``: executes an approved proposal once (replays return the stored receipt).
    - ``actor``: the approval actor used by ``propose``.
    """

    actor = ACTOR

    def __init__(self, world: World, database: Database) -> None:
        self.world = world
        self.database = database
        self.approvals = ApprovalService(database)

    def specs(self) -> list[ToolSpec]:
        return [spec.model_copy(deep=True) for spec in TOOL_SPECS]

    def intent(self, name: str, arguments: dict[str, Any] | str) -> Any:
        from ..rules import ToolIntent  # lazy: rules/ is built separately

        args = _arguments(arguments)
        if name == "memory_search":
            return ToolIntent(tool="memory", action="search", target=None, sensitivity="personal")
        if name == "reminder_set":
            return ToolIntent(tool="reminders", action="create", target=None, sensitivity="low")
        if name == "gmail_draft_create":
            return ToolIntent(tool="gmail", action="draft_create", target=args.get("to"), sensitivity="personal")
        if name == "gmail_delete_message":
            return ToolIntent(
                tool="gmail", action="delete", target=f"message:{args.get('message_id', '')}", sensitivity="personal"
            )
        raise KeyError(f"unknown tool {name!r}")

    def run(self, name: str, arguments: dict[str, Any] | str, *, task_id: str, call_id: str) -> str:
        args = _arguments(arguments)
        self.world.executions.append((name, args))
        if name == "memory_search":
            return json.dumps({"results": ["Nico prefers short answers."]})
        if name == "reminder_set":
            run_at = self.world.clock() + timedelta(minutes=float(args["minutes"]))
            self.database.migrate()
            with self.database.connect() as connection:
                with self.database.transaction(connection):
                    job = ReminderStore.create(
                        connection,
                        run_at=run_at,
                        task_id=task_id,
                        destination="desktop:owner",
                        text=str(args["text"]),
                        idempotency_key=f"reminder:{task_id}:{call_id}",
                    )
            return json.dumps({"reminder_id": job.id, "run_at": job.run_at.isoformat()})
        if name == "gmail_delete_message":
            self.world.gmail.delete_message(str(args.get("message_id", "")))
            return json.dumps({"deleted": True})
        raise KeyError(f"tool {name!r} cannot run without approval or is unknown")

    def propose(self, name: str, arguments: dict[str, Any] | str, *, task_id: str) -> Any:
        from ..gmail import GmailActions

        args = _arguments(arguments)
        if name != "gmail_draft_create":
            raise KeyError(f"tool {name!r} has no approval flow")
        return GmailActions(self.database, self.approvals, self.world.gmail).propose_draft(
            actor=self.actor, to=args["to"], subject=args["subject"], body=args["body"]
        )

    def run_approved(self, approval_id: str) -> str:
        from ..gmail import GmailActions
        from ..policy import PolicyError

        token = self.world.tokens.get(approval_id)
        if token is None:
            raise PolicyError("approval has not been approved by the user")
        receipt = GmailActions(self.database, self.approvals, self.world.gmail).execute(
            approval_id, actor=self.actor, token=token
        )
        if not receipt.replayed:
            self.world.executions.append(("gmail_draft_create", {"approval_id": approval_id}))
        return receipt.model_dump_json()


def _arguments(arguments: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(arguments, str):
        return json.loads(arguments) if arguments.strip() else {}
    return dict(arguments)


@dataclass
class Stack:
    """One running daemon: the loop plus everything it was built from."""

    world: World
    database: Database
    tools: ScenarioTools
    catalog: list[ModelInfo]
    budgets: Any = None
    registry: ProviderRegistry | None = None
    router: Any = None
    meter: Any = None
    rules: Any = None
    packer: Any = None
    reviewer: Any = None
    loop: Any = None

    @property
    def provider(self) -> ScriptedProvider:
        return self.world.provider

    @property
    def clock(self) -> FakeClock:
        return self.world.clock

    @property
    def approvals(self) -> ApprovalService:
        return self.tools.approvals

    def advance_time(self, **kwargs: float) -> datetime:
        """Move the injected clock forward (``minutes=10``, ``days=1`` ...)."""
        return self.world.clock.advance(**kwargs)

    def run(self, task_id: str) -> list[Any]:
        """Drain ``loop.run(task_id)`` and return its events."""
        return list(self.loop.run(task_id))

    def start_and_run(self, message: str, **kwargs: Any) -> tuple[str, list[Any]]:
        task_id = self.loop.start_task(message, **kwargs)
        return task_id, self.run(task_id)

    def approve(self, approval_id: str) -> None:
        """The user approves in the UI: issue the one-time token and hand it to the executor."""
        issued = self.approvals.approve(approval_id, actor=self.tools.actor, now=self.clock())
        self.world.tokens[approval_id] = issued.token

    def run_due_jobs(self) -> list[Any]:
        return JobRunner(self.database).run_due(self.clock())

    def restart(self) -> Stack:
        """A brand-new stack over the same database file and the same outside world (a daemon restart)."""
        return _assemble(self.world, self.catalog, self.budgets)


def build_world(
    tmp_dir: Path | str,
    *,
    script: Sequence[Turn] = (),
    catalog: Sequence[ModelInfo] | None = None,
    clock: FakeClock | None = None,
    extra_providers: Sequence[ScriptedProvider] = (),
) -> Stack:
    """Database, scripted provider, clock and tools only. Needs none of the M2 modules."""
    provider = ScriptedProvider(script, catalog=catalog)
    world = World(
        db_path=Path(tmp_dir) / "opendot.db",
        provider=provider,
        extra_providers=list(extra_providers),
        clock=clock or FakeClock(),
    )
    database = Database(world.db_path)
    database.migrate()
    return Stack(
        world=world,
        database=database,
        tools=ScenarioTools(world, database),
        catalog=provider.list_models(),
    )


def build_stack(
    tmp_dir: Path | str,
    *,
    script: Sequence[Turn] = (),
    catalog: Sequence[ModelInfo] | None = None,
    budgets: Any = None,
    clock: FakeClock | None = None,
    extra_providers: Sequence[ScriptedProvider] = (),
) -> Stack:
    """Assemble the full stack. ``extra_providers`` are registered *and enabled* (opt-in providers)."""
    base = build_world(tmp_dir, script=script, catalog=catalog, clock=clock, extra_providers=extra_providers)
    return _assemble(base.world, base.catalog, budgets)


def make_budgets(*, task_credits: float = 5.0, daily_credits: float = 50.0) -> Any:
    return load_attr("opendot_core.usage.meter", "Budgets")(task_credits=task_credits, daily_credits=daily_credits)


def load_attr(module: str, name: str) -> Any:
    """Import ``module.name`` lazily; raise ``StackUnavailable`` when it is not built yet."""
    try:
        return getattr(importlib.import_module(module), name)
    except ImportError as error:
        raise StackUnavailable(module, str(error)) from error
    except AttributeError as error:
        raise StackUnavailable(module, f"{name} is missing") from error


def _construct(cls: Any, **candidates: Any) -> Any:
    accepted = inspect.signature(cls).parameters
    return cls(**{key: value for key, value in candidates.items() if key in accepted})


def _assemble(world: World, catalog: list[ModelInfo], budgets: Any) -> Stack:
    router_cls = load_attr("opendot_core.router", "Router")
    meter_cls = load_attr("opendot_core.usage.meter", "UsageMeter")
    rules_cls = load_attr("opendot_core.rules", "RuleEngine")
    packer_cls = load_attr("opendot_core.agent.packer", "PromptPacker")
    reviewer_cls = load_attr("opendot_core.agent.reviewer", "Reviewer")
    loop_cls = load_attr("opendot_core.agent.loop", "AgentLoop")

    database = Database(world.db_path)
    database.migrate()
    tools = ScenarioTools(world, database)

    every = [world.provider, *world.extra_providers]
    registry = ProviderRegistry(ProviderSettings(enabled={PLAN_PROVIDER, *(p.name for p in every)}))
    for provider in every:
        registry.register(provider)

    router = _construct(router_cls, catalog=catalog)
    meter = _construct(meter_cls, database=database, budgets=budgets, clock=world.clock)
    rules = _construct(rules_cls, database=database)
    packer = _construct(packer_cls)
    reviewer: Callable[..., Any] = _construct(
        reviewer_cls,
        database=database,
        registry=registry,
        router=router,
        meter=meter,
        use_model=False,
        model_pass=False,
        clock=world.clock,
    )
    loop = _construct(
        loop_cls,
        database=database,
        registry=registry,
        router=router,
        meter=meter,
        rules=rules,
        packer=packer,
        tools=tools,
        reviewer=reviewer,
        clock=world.clock,
        provider_name=PLAN_PROVIDER,
    )
    return Stack(
        world=world,
        database=database,
        tools=tools,
        catalog=catalog,
        budgets=budgets,
        registry=registry,
        router=router,
        meter=meter,
        rules=rules,
        packer=packer,
        reviewer=reviewer,
        loop=loop,
    )
