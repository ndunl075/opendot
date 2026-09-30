"""Built-in default rules (ARCHITECTURE.md section 10), one explicit entry per MCP tool."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DefaultRule:
    tool: str
    behavior: str
    max_sensitivity: str
    note: str


def _entries(tools: tuple[str, ...], behavior: str, max_sensitivity: str, note: str) -> list[DefaultRule]:
    return [DefaultRule(tool, behavior, max_sensitivity, note) for tool in tools]


DEFAULT_RULES: tuple[DefaultRule, ...] = tuple(
    _entries(
        (
            "system_status",
            "agenda_get",
            "memory_search",
            "profile_get",
            "brief_get",
            "connector_status",
            "connector_records_get",
            "important_dates_get",
            "threads_awaiting_reply",
            "availability_get",
            "pull_requests_get",
            "journal_get",
            "composio_search",
            "composio_status",
        ),
        "auto",
        "sensitive",
        "reading and syncing connected apps",
    )
    + _entries(
        ("remember", "memory_correct", "memory_feedback", "mood_record", "gratitude_record"),
        "auto",
        "personal",
        "saving the user's messages and low-risk memory (visible in the audit log)",
    )
    + _entries(
        ("task_upsert", "task_complete", "reminder_set", "nag_until_done", "task_schedule", "important_date_set"),
        "auto",
        "sensitive",
        "local tasks and reminders (reversible)",
    )
    + _entries(("action_commit",), "auto", "secret", "executes only an approval the user already granted")
    + _entries(("calendar_event_propose",), "ask", "secret", "creating Calendar events")
    + _entries(("message_draft",), "ask", "secret", "creating Gmail drafts")
    + _entries(("github_issue_propose",), "ask", "secret", "creating GitHub issues")
    + _entries(("message_send_propose",), "ask", "secret", "sending messages: ask every time")
    + _entries(("forget",), "ask", "secret", "deleting the user's own memory")
    + _entries(("composio_connect", "composio_execute"), "ask", "secret", "changes in outside apps")
)

DEFAULT_BY_TOOL: dict[str, DefaultRule] = {rule.tool: rule for rule in DEFAULT_RULES}

UNMATCHED_BEHAVIOR = "ask"

ASK_EVERY_TIME_TOOLS = frozenset(
    {"message_send_propose", "gmail_message_send", "github_pr_comment_create", "slack_post", "telegram_send"}
)
"""Tools and approval types that send or post for the user: always ``ask`` (section 10)."""
_SEND_WORDS = frozenset(
    {
        "send", "sends", "sent", "post", "posts", "publish", "tweet", "broadcast", "forward", "comment",
        "comments", "invite", "share", "announce", "dm", "chat", "message_send",
    }
)
_SPLIT = re.compile(r"[^a-z0-9]+")


def is_send_or_post(tool: str, action: str) -> bool:
    """True for an intent that sends, posts, comments or publishes to other people.

    Outside-app tools only: OpenDot's own local tools (memory, tasks, reminders ...) never reach
    other people. Matching errs toward asking, which is always safe.
    """
    from .deny_list import is_local_tool

    if tool in ASK_EVERY_TIME_TOOLS or action in ASK_EVERY_TIME_TOOLS:
        return True
    if is_local_tool(tool):
        return False
    words = {part for part in _SPLIT.split(f"{tool} {action}".lower()) if part}
    return bool(words & _SEND_WORDS)
