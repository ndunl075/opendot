"""Rules endpoints (``rules_list``, ``rule_create``, ``rule_update``, ``rule_delete``).

Backed by ``rules.RuleEngine``. The contract's rule is a friendlier view of the engine's rule:

- ``action`` is the engine's ``tool`` and ``action`` joined by a dot: ``message_draft`` (any action of
  that tool) or ``calendar_event_propose.create`` (tool and action). Patterns (``*``) work as in the engine.
- ``name`` and ``enabled`` live in the ``rule_names`` and ``rules_disabled`` settings (the rules table
  has no such columns). A disabled rule is kept but ignored by ``RuleEngine.decide``.
- ``description`` is the engine's ``note``.
- ``locked`` is true for the core deny list and for the built-in defaults; ``core_deny`` only for the
  deny list. Neither can be edited or deleted (403 ``rule_locked``): to loosen a default, add a more
  specific rule; the deny list cannot be loosened by anything.
- A user rule that lets something run without asking (``auto``, ``auto_if_preapproved``) is capped at
  "sensitive" data, like always-allow rules; the contract has no sensitivity field to widen it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ...rules import Behavior, RuleEngine, RuleError
from ...rules.deny_list import deny_item_for_pattern
from ...rules.engine import Rule as EngineRule
from ..models import OkResponse, Rule, RuleCreateRequest, RuleList, RuleUpdateRequest
from .config_support import error, ok, parse_body, run, settings_store

if TYPE_CHECKING:
    from . import ApiContext

_FROM_APPROVAL = re.compile(r"from approval (\S+)$")
_NAMES_KEY = "rule_names"
_DISABLED_KEY = "rules_disabled"


def split_action(action: str) -> tuple[str, str]:
    """Contract ``action`` to the engine's ``(tool, action)``."""
    action = action.strip()
    tool, dot, rest = action.partition(".")
    return (tool, rest or "*") if dot else (action, "*")


def join_action(tool: str, action: str) -> str:
    return tool if action in ("", "*") else f"{tool}.{action}"


def routes(ctx: ApiContext) -> list[Route]:
    if ctx.database is None or ctx.rules is None:
        return []
    engine: RuleEngine = ctx.rules
    store = settings_store(ctx)

    def names() -> dict[str, str]:
        return store.get(_NAMES_KEY, dict[str, str], {})

    def to_contract(rule: EngineRule, names_: dict[str, str], disabled: set[str]) -> Rule:
        label = join_action(rule.tool, rule.action)
        fallback = rule.note or label
        if rule.target:
            fallback = f"{fallback} ({rule.target})" if rule.note is None else fallback
        approval = _FROM_APPROVAL.search(rule.note or "")
        return Rule(
            id=rule.id,
            name=(names_.get(rule.id) or fallback)[:80] or label,
            action=label,
            behavior=rule.behavior.value,
            enabled=rule.id not in disabled,
            locked=rule.locked or rule.builtin,
            core_deny=rule.locked,
            description=rule.note,
            created_at=rule.created_at,
            created_from_approval_id=approval.group(1) if approval else None,
        )

    def view(rule: EngineRule) -> Rule:
        return to_contract(rule, names(), engine.disabled_ids())

    def set_enabled(rule_id: str, enabled: bool) -> None:
        disabled = engine.disabled_ids()
        disabled.discard(rule_id) if enabled else disabled.add(rule_id)
        store.set(_DISABLED_KEY, sorted(disabled))

    def set_name(rule_id: str, name: str | None) -> None:
        current = names()
        if name:
            current[rule_id] = name
        else:
            current.pop(rule_id, None)
        store.set(_NAMES_KEY, current)

    def find(rule_id: str) -> EngineRule | None:
        return next((rule for rule in engine.list_rules() if rule.id == rule_id), None)

    def locked_response(rule: EngineRule) -> Response:
        if rule.locked:
            return error(403, "rule_locked", "This is a core deny-list item. Nothing can change or remove it.")
        return error(
            403, "rule_locked", "Built-in rules cannot be edited or deleted. Add a more specific rule instead."
        )

    async def rules_list(_: Request) -> Response:
        def build() -> RuleList:
            names_, disabled = names(), engine.disabled_ids()
            return RuleList(rules=[to_contract(rule, names_, disabled) for rule in engine.list_rules()])

        return ok(await run(build))

    async def rule_create(request: Request) -> Response:
        body = await parse_body(request, RuleCreateRequest)
        if isinstance(body, Response):
            return body
        tool, action = split_action(body.action)
        if not tool:
            return error(422, "invalid_request", "action: a rule needs a tool or action pattern")
        denied = deny_item_for_pattern(body.action) or deny_item_for_pattern(action)
        if denied is not None:
            return error(403, "rule_locked", f"'{body.action}' is on the core deny list ({denied.label}); no rule can change it")
        looser = body.behavior in ("auto", "auto_if_preapproved")

        def create() -> Rule:
            rule = engine.add_rule(
                tool=tool, action=action, behavior=Behavior(body.behavior),
                max_sensitivity="sensitive" if looser else "secret", created_by="user", note=body.description,
            )
            set_name(rule.id, body.name.strip())
            return view(rule)

        try:
            return ok(await run(create))
        except RuleError as failure:
            message = str(failure)
            if "core deny list" in message:
                return error(403, "rule_locked", message)
            return error(422, "invalid_rule", message)

    async def rule_update(request: Request) -> Response:
        rule_id = request.path_params["rule_id"]
        body = await parse_body(request, RuleUpdateRequest)
        if isinstance(body, Response):
            return body
        existing = await run(find, rule_id)
        if existing is None:
            return error(404, "not_found", "No such rule.")
        if existing.locked or existing.builtin:
            return locked_response(existing)

        def update() -> Rule:
            changes: dict[str, object] = {}
            if body.behavior is not None:
                changes["behavior"] = body.behavior
            if "description" in body.model_fields_set:
                changes["note"] = body.description
            rule = engine.update_rule(rule_id, **changes) if changes else existing
            if body.name is not None:
                set_name(rule_id, body.name.strip() or None)
            if body.enabled is not None:
                set_enabled(rule_id, body.enabled)
            return view(rule)

        try:
            return ok(await run(update))
        except RuleError as failure:
            return error(422, "invalid_rule", str(failure))

    async def rule_delete(request: Request) -> Response:
        rule_id = request.path_params["rule_id"]
        existing = await run(find, rule_id)
        if existing is None:
            return error(404, "not_found", "No such rule.")
        if existing.locked or existing.builtin:
            return locked_response(existing)

        def delete() -> None:
            engine.delete_rule(rule_id)
            set_name(rule_id, None)
            set_enabled(rule_id, True)

        try:
            await run(delete)
        except RuleError as failure:
            return error(404, "not_found", str(failure))
        return ok(OkResponse())

    v = "/v1"
    return [
        Route(f"{v}/rules", rules_list, methods=["GET"]),
        Route(f"{v}/rules", rule_create, methods=["POST"]),
        Route(f"{v}/rules/{{rule_id}}", rule_update, methods=["PUT"]),
        Route(f"{v}/rules/{{rule_id}}", rule_delete, methods=["DELETE"]),
    ]
