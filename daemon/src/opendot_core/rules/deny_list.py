"""The core deny list: intents the companion must never perform, whatever any rule says.

Matching is on the intent's action alone (plus, for the generic Composio passthrough,
keywords in the action slug), so the engine can check it before it reads the rules table.
Nothing here is configurable at runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase


@dataclass(frozen=True)
class DenyItem:
    id: str
    label: str
    actions: frozenset[str]
    keywords: tuple[str, ...]


DENY_LIST: tuple[DenyItem, ...] = (
    DenyItem(
        id="deny:delete_external",
        label="Deleting data in outside apps",
        actions=frozenset({"delete_external", "external_delete"}),
        keywords=("delete", "trash", "remove", "purge", "destroy", "erase"),
    ),
    DenyItem(
        id="deny:spend_money",
        label="Spending money",
        actions=frozenset({"spend_money", "purchase", "payment", "transfer_funds"}),
        keywords=("purchase", "payment", "checkout", "charge", "refund", "transfer_funds", "buy", "pay"),
    ),
    DenyItem(
        id="deny:security_change",
        label="Security changes",
        actions=frozenset({"security_change", "change_security", "grant_access", "share_publicly"}),
        keywords=("permission", "2fa", "mfa", "security", "access_token"),
    ),
    DenyItem(
        id="deny:credential_change",
        label="Credential and password changes",
        actions=frozenset({"credential_change", "change_credentials", "password_change", "change_password"}),
        keywords=("password", "credential", "api_key", "passkey"),
    ),
)

# Only the generic passthrough to outside apps is matched by keyword; first-party tools
# (for example the local forget) use explicit actions.
_KEYWORD_TOOLS = ("composio_",)
_SPLIT = re.compile(r"[^a-z0-9]+")


def _words(action: str) -> set[str]:
    return {part for part in _SPLIT.split(action.lower()) if part}


def deny_item_for(tool: str, action: str) -> DenyItem | None:
    """Return the deny-list item an intent falls under, or None."""
    lowered = action.strip().lower()
    for item in DENY_LIST:
        if lowered in item.actions:
            return item
    if tool.startswith(_KEYWORD_TOOLS):
        words = _words(lowered)
        joined = "_".join(sorted(words))
        for item in DENY_LIST:
            if any(keyword in words or (len(keyword) > 4 and keyword in joined) for keyword in item.keywords):
                return item
    return None


def deny_item_for_pattern(action_pattern: str) -> DenyItem | None:
    """Return the item a rule's action pattern explicitly names (not a bare ``*``), or None.

    Used by ``add_rule`` to reject rules aimed at the deny list.
    """
    pattern = action_pattern.strip().lower()
    if pattern in {"", "*"}:
        return None
    for item in DENY_LIST:
        if any(fnmatchcase(name, pattern) for name in item.actions):
            return item
    if not any(ch in pattern for ch in "*?["):
        return deny_item_for("composio_execute", pattern)
    return None
