"""The rule engine: decide what the companion may do with a tool call.

Decision order:

1. The core deny list (``deny_list.py``) is checked first and never reads the rules table,
   so no stored rule, however it got there, can change a deny-list outcome.
2. Stored rules and the built-in defaults are matched on tool, action, target, data
   sensitivity and cost. The most specific match wins; equal specificity goes to the
   stricter behavior (handoff > ask > auto_if_preapproved > auto). To loosen a default,
   add a more specific rule (a narrower target, action, sensitivity cap or cost cap).
3. No match means ``ask``.

Decisions can be recorded with ``audit_decision`` (tool, behavior, rule id, reason; never
message content).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from ..audit import AuditEvent, AuditLog
from ..composio import ACTION_TYPE as COMPOSIO_ACTION_TYPE
from ..db import Database
from ..policy import ApprovalService
from .defaults import DEFAULT_RULES, UNMATCHED_BEHAVIOR, is_send_or_post
from .deny_list import DENY_LIST, deny_item_for, deny_item_for_pattern

SENSITIVITIES = ("public", "personal", "sensitive", "secret")


class RuleError(ValueError):
    """A rule change or always-allow request that the rules policy refuses."""


class Behavior(StrEnum):
    AUTO = "auto"
    AUTO_IF_PREAPPROVED = "auto_if_preapproved"
    ASK = "ask"
    HANDOFF = "handoff"


_STRICTNESS = {
    Behavior.AUTO: 0,
    Behavior.AUTO_IF_PREAPPROVED: 1,
    Behavior.ASK: 2,
    Behavior.HANDOFF: 3,
}


class ToolIntent(BaseModel):
    tool: str
    action: str
    target: str | None = None
    sensitivity: str
    cost_credits: float = 0.0
    user_preapproved: bool = False
    reaches_people: bool = False
    """Declared by the tool executor: the action sends, posts or publishes to other people."""


class Rule(BaseModel):
    id: str
    tool: str
    action: str = "*"
    target: str | None = None
    max_sensitivity: str = "secret"
    behavior: Behavior
    created_by: str
    created_at: datetime
    note: str | None = None
    max_cost_credits: float | None = None
    locked: bool = False
    builtin: bool = False


class Decision(BaseModel):
    behavior: Behavior
    rule_id: str | None
    reason: str
    locked: bool = False


# approval action_type -> (MCP tool, action, preview key used as the target).
# Only these proposals can become always-allow rules. Sending, posting and deleting
# proposals are deliberately absent: they stay "ask every time" or belong to the deny list.
_ALWAYS_ALLOW_TYPES: dict[str, tuple[str, str, str | None]] = {
    "calendar_event_create": ("calendar_event_propose", "*", "calendar_id"),
    "gmail_draft_create": ("message_draft", "*", "to"),
    "github_issue_create": ("github_issue_propose", "*", "repository"),
    COMPOSIO_ACTION_TYPE: ("composio_execute", "", None),
}


def _spec(pattern: str | None) -> int:
    if pattern is None or pattern == "*":
        return 0
    return 1 if any(ch in pattern for ch in "*?[") else 2


def _matches(pattern: str | None, value: str | None) -> bool:
    if pattern is None or pattern == "*":
        return True
    return value is not None and fnmatchcase(value, pattern)


def _sens_rank(level: str) -> int:
    if level not in SENSITIVITIES:
        raise RuleError(f"unknown sensitivity: {level}")
    return SENSITIVITIES.index(level)


class RuleEngine:
    def __init__(self, database: Database) -> None:
        self.database = database

    # -- decisions ---------------------------------------------------------------

    def decide(self, intent: ToolIntent) -> Decision:
        denied = deny_item_for(intent.tool, intent.action)
        if denied is not None:
            return Decision(
                behavior=Behavior.HANDOFF,
                rule_id=denied.id,
                reason=f"core deny list: {denied.label}; do it yourself",
                locked=True,
            )
        level = _sens_rank(intent.sensitivity)
        best: tuple[tuple[int, ...], int, Rule] | None = None
        for rule in self._all_rules():
            if rule.locked or not self._applies(rule, intent, level):
                continue
            key = (self._specificity(rule), _STRICTNESS[rule.behavior])
            if best is None or key > (best[0], best[1]):
                best = (key[0], key[1], rule)
        if best is None:
            return Decision(
                behavior=Behavior(UNMATCHED_BEHAVIOR), rule_id=None, reason="no matching rule; asking by default"
            )
        rule = best[2]
        if rule.behavior is Behavior.AUTO_IF_PREAPPROVED and not intent.user_preapproved:
            return Decision(
                behavior=Behavior.ASK,
                rule_id=rule.id,
                reason="rule allows this only when the user asked for this exact action; they did not",
            )
        # A satisfied auto_if_preapproved resolves to plain auto, so callers only see auto, ask or handoff.
        behavior = Behavior.AUTO if rule.behavior is Behavior.AUTO_IF_PREAPPROVED else rule.behavior
        if behavior is Behavior.AUTO and (intent.reaches_people or is_send_or_post(intent.tool, intent.action)):
            # Section 10: sending, posting and publishing ask every time; no rule relaxes that.
            return Decision(
                behavior=Behavior.ASK,
                rule_id=rule.id,
                reason="sending, posting and publishing ask every time",
                locked=True,
            )
        return Decision(behavior=behavior, rule_id=rule.id, reason=rule.note or f"matched rule {rule.id}")

    def audit_decision(
        self,
        intent: ToolIntent,
        decision: Decision,
        *,
        actor: str = "rules",
        correlation_id: str | None = None,
    ) -> str:
        """Append one audit event for a decision. Carries no message content or target."""
        return AuditLog(self.database).append(
            AuditEvent(
                actor=actor,
                client="rules",
                tool="rule_decision",
                outcome=decision.behavior.value,
                arguments={"tool": intent.tool, "action": intent.action, "sensitivity": intent.sensitivity},
                result={
                    "tool": intent.tool,
                    "behavior": decision.behavior.value,
                    "rule_id": decision.rule_id,
                    "reason": decision.reason,
                    "locked": decision.locked,
                },
                correlation_id=correlation_id,
            )
        )

    # -- rule management ---------------------------------------------------------

    def add_rule(
        self,
        rule: Rule | None = None,
        /,
        *,
        tool: str | None = None,
        behavior: Behavior | str | None = None,
        action: str = "*",
        target: str | None = None,
        max_sensitivity: str = "secret",
        max_cost_credits: float | None = None,
        created_by: str = "user",
        note: str | None = None,
    ) -> Rule:
        """Store a user rule, given as keywords or as a ``Rule``. Deny-list rules are always rejected.

        A passed ``Rule`` is validated exactly like keywords; its ``locked`` and ``builtin`` flags
        are ignored (only the core deny list is locked, and it is never stored).
        """
        if rule is not None:
            tool, action, target = rule.tool, rule.action, rule.target
            behavior, max_sensitivity, max_cost_credits = rule.behavior, rule.max_sensitivity, rule.max_cost_credits
            created_by, note = rule.created_by, rule.note
        if tool is None or behavior is None:
            raise RuleError("a rule needs a tool and a behavior")
        fields = self._validated(tool, action, target, behavior, max_sensitivity, max_cost_credits)
        stored = Rule(
            id=rule.id if rule is not None and rule.id else str(uuid4()),
            created_by=created_by,
            created_at=datetime.now(UTC),
            note=note,
            **fields,
        )
        self._insert(stored)
        return stored

    def update_rule(self, rule_id: str, **changes: Any) -> Rule:
        allowed = {"tool", "action", "target", "behavior", "max_sensitivity", "max_cost_credits", "note"}
        unknown = set(changes) - allowed
        if unknown:
            raise RuleError(f"cannot update: {', '.join(sorted(unknown))}")
        current = self._get_stored(rule_id)
        merged = {
            "tool": current.tool,
            "action": current.action,
            "target": current.target,
            "behavior": current.behavior,
            "max_sensitivity": current.max_sensitivity,
            "max_cost_credits": current.max_cost_credits,
        }
        merged.update({key: value for key, value in changes.items() if key != "note"})
        fields = self._validated(**merged)
        note = changes.get("note", current.note)
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    UPDATE rules SET tool = ?, action = ?, target = ?, max_sensitivity = ?, behavior = ?,
                        max_cost_credits = ?, note = ? WHERE id = ?
                    """,
                    (
                        fields["tool"],
                        fields["action"],
                        fields["target"],
                        fields["max_sensitivity"],
                        fields["behavior"].value,
                        fields["max_cost_credits"],
                        note,
                        rule_id,
                    ),
                )
        return self._get_stored(rule_id)

    def delete_rule(self, rule_id: str) -> None:
        self._get_stored(rule_id)
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute("DELETE FROM rules WHERE id = ?", (rule_id,))

    def list_rules(self, *, include_defaults: bool = True) -> list[Rule]:
        """Stored rules, then built-in defaults, then the locked deny-list entries."""
        rules = self._stored_rules()
        if include_defaults:
            rules += self._default_rules()
        return rules + self._deny_rules()

    def always_allow(self, approval_id: str, actor: str) -> Rule:
        """Create an ``auto`` rule from an approved proposal's tool, action and target."""
        approval = ApprovalService(self.database).get(approval_id)
        if approval is None:
            raise RuleError("approval does not exist")
        if approval.state not in {"approved", "consumed"}:
            raise RuleError(f"approval is not approved: {approval.state}")
        mapping = _ALWAYS_ALLOW_TYPES.get(approval.action_type)
        if mapping is None:
            raise RuleError(f"'{approval.action_type}' proposals cannot be made always-allow")
        tool, action, target_key = mapping
        if approval.action_type == COMPOSIO_ACTION_TYPE:
            action = str(approval.preview.get("slug") or "")
            if not action:
                raise RuleError("approval has no tool slug to allow")
        if deny_item_for(tool, action) is not None:
            raise RuleError("this action is on the core deny list; no rule can allow it")
        target = None
        if target_key is not None:
            raw = approval.preview.get(target_key)
            target = str(raw) if raw else None
            if target is None:
                raise RuleError("approval has no target to allow; refusing an unbounded rule")
        rule = self.add_rule(
            tool=tool,
            action=action or "*",
            target=target,
            behavior=Behavior.AUTO,
            max_sensitivity="sensitive",
            created_by=actor,
            note=f"always allow, from approval {approval_id}",
        )
        return rule

    # -- internals ---------------------------------------------------------------

    @staticmethod
    def _applies(rule: Rule, intent: ToolIntent, level: int) -> bool:
        if not (_matches(rule.tool, intent.tool) and _matches(rule.action, intent.action)):
            return False
        if not _matches(rule.target, intent.target):
            return False
        if level > _sens_rank(rule.max_sensitivity):
            return False
        return rule.max_cost_credits is None or intent.cost_credits <= rule.max_cost_credits

    @staticmethod
    def _specificity(rule: Rule) -> tuple[int, ...]:
        return (
            _spec(rule.tool),
            _spec(rule.action),
            _spec(rule.target),
            len(SENSITIVITIES) - 1 - _sens_rank(rule.max_sensitivity),
            0 if rule.max_cost_credits is None else 1,
        )

    @staticmethod
    def _validated(
        tool: str,
        action: str,
        target: str | None,
        behavior: Behavior | str,
        max_sensitivity: str,
        max_cost_credits: float | None,
    ) -> dict[str, Any]:
        if not tool or not tool.strip():
            raise RuleError("rule tool cannot be empty")
        action = (action or "*").strip()
        try:
            behavior = Behavior(behavior)
        except ValueError as error:
            raise RuleError(f"unknown behavior: {behavior}") from error
        _sens_rank(max_sensitivity)
        if max_cost_credits is not None and max_cost_credits < 0:
            raise RuleError("max_cost_credits cannot be negative")
        item = deny_item_for_pattern(action)
        if item is None and not any(ch in action for ch in "*?["):
            item = deny_item_for(tool.strip(), action)
        if item is not None:
            raise RuleError(f"rejected: '{action}' is on the core deny list ({item.label}); no rule can change it")
        return {
            "tool": tool.strip(),
            "action": action,
            "target": target,
            "behavior": behavior,
            "max_sensitivity": max_sensitivity,
            "max_cost_credits": max_cost_credits,
        }

    def _all_rules(self) -> list[Rule]:
        return self._stored_rules() + self._default_rules()

    def _stored_rules(self) -> list[Rule]:
        self.database.migrate()
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM rules ORDER BY created_at, id").fetchall()
        rules: list[Rule] = []
        for row in rows:
            try:
                rules.append(self._from_row(row))
            except (ValueError, RuleError):
                continue  # a hand-edited row with an unknown value is ignored, which falls back to ask
        return rules

    @staticmethod
    def _default_rules() -> list[Rule]:
        epoch = datetime(2026, 1, 1, tzinfo=UTC)
        return [
            Rule(
                id=f"default:{entry.tool}",
                tool=entry.tool,
                behavior=Behavior(entry.behavior),
                max_sensitivity=entry.max_sensitivity,
                created_by="default",
                created_at=epoch,
                note=entry.note,
                builtin=True,
            )
            for entry in DEFAULT_RULES
        ]

    @staticmethod
    def _deny_rules() -> list[Rule]:
        epoch = datetime(2026, 1, 1, tzinfo=UTC)
        return [
            Rule(
                id=item.id,
                tool="*",
                action=sorted(item.actions)[0],
                behavior=Behavior.HANDOFF,
                created_by="core",
                created_at=epoch,
                note=item.label,
                locked=True,
                builtin=True,
            )
            for item in DENY_LIST
        ]

    def _get_stored(self, rule_id: str) -> Rule:
        if rule_id.startswith(("default:", "deny:")):
            raise RuleError("built-in and deny-list rules cannot be changed")
        self.database.migrate()
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
        if row is None:
            raise RuleError("rule does not exist")
        return self._from_row(row)

    def _insert(self, rule: Rule) -> None:
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    INSERT INTO rules (id, tool, action, target, max_sensitivity, behavior, created_by,
                        created_at, note, max_cost_credits) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rule.id,
                        rule.tool,
                        rule.action,
                        rule.target,
                        rule.max_sensitivity,
                        rule.behavior.value,
                        rule.created_by,
                        rule.created_at.isoformat(),
                        rule.note,
                        rule.max_cost_credits,
                    ),
                )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Rule:
        _sens_rank(row["max_sensitivity"])
        return Rule(
            id=row["id"],
            tool=row["tool"],
            action=row["action"],
            target=row["target"],
            max_sensitivity=row["max_sensitivity"],
            behavior=Behavior(row["behavior"]),
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
            note=row["note"],
            max_cost_credits=row["max_cost_credits"],
        )
