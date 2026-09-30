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

# Tools that act only on OpenDot's own local data (memory, tasks, reminders ...). Their
# deletions are the user's own reversible actions (section 10: Forget, Reset and cancelling a
# reminder), so they use explicit actions only. Every other tool reaches an outside app
# (Gmail, Calendar, GitHub, Composio's passthrough, any connector added later), so its action
# is also matched by keyword: an unknown outside tool that deletes, pays or changes security
# is denied by default rather than allowed by omission.
_LOCAL_TOOL_PREFIXES = (
    "memory",
    "remember",
    "forget",
    "reminder",
    "task",
    "nag",
    "important_date",
    "mood",
    "gratitude",
    "journal",
    "profile",
    "agenda",
    "brief",
    "system_status",
    "connector_status",
    "action_commit",
)
_SPLIT = re.compile(r"[^a-z0-9]+")


def is_local_tool(tool: str) -> bool:
    return tool.strip().lower().startswith(_LOCAL_TOOL_PREFIXES)


def _words(action: str) -> set[str]:
    return {part for part in _SPLIT.split(action.lower()) if part}


def deny_item_for(tool: str, action: str) -> DenyItem | None:
    """Return the deny-list item an intent falls under, or None."""
    lowered = action.strip().lower()
    for item in DENY_LIST:
        if lowered in item.actions:
            return item
    if not is_local_tool(tool):
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
