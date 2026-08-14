"""Deterministic per-turn MCP tool selection for Alfred's Hermes subprocess."""

from __future__ import annotations

import re

HERMES_MCP_TOOL_FILTER_ENV = "ALFRED_HERMES_MCP_TOOLS"
MAX_HERMES_TOOLS_PER_TURN = 8

_TASK_TERMS = re.compile(
    r"\b(?:agenda|task|tasks|to-?do|deadline|due|flag|note|remind|reminder)\b",
    re.IGNORECASE,
)
_TASK_CREATE_TERMS = re.compile(
    r"\b(?:add|create|flag|make|note|remember to|save|set)\b", re.IGNORECASE
)
_TASK_COMPLETE_TERMS = re.compile(
    r"\b(?:complete|completed|done|finish|finished|mark off)\b", re.IGNORECASE
)
_REMINDER_TERMS = re.compile(r"\b(?:remind|reminder)\b", re.IGNORECASE)
_CALENDAR_TERMS = re.compile(
    r"\b(?:agenda|appointment|calendar|event|meeting|schedule)\b", re.IGNORECASE
)
_CALENDAR_WRITE_TERMS = re.compile(
    r"\b(?:add|book|create|move|reschedule|schedule|set up)\b", re.IGNORECASE
)
_MAIL_TERMS = re.compile(r"\b(?:email|gmail|inbox|mail|message|reply)\b", re.IGNORECASE)
_MAIL_DRAFT_TERMS = re.compile(r"\b(?:compose|draft|reply|respond|write)\b", re.IGNORECASE)
_MAIL_SEND_TERMS = re.compile(r"\b(?:send|email them|message them)\b", re.IGNORECASE)
_GITHUB_TERMS = re.compile(
    r"\b(?:github|issue|pull request|repo|repository)\b", re.IGNORECASE
)
_GITHUB_WRITE_TERMS = re.compile(
    r"\b(?:create|file|open|report)\b.*\bissue\b|\bissue\b.*\b(?:create|file|open|report)\b",
    re.IGNORECASE,
)
_MEMORY_TERMS = re.compile(
    r"\b(?:forget|memory|memories|preference|profile|remember|you know about me)\b",
    re.IGNORECASE,
)
_MEMORY_WRITE_TERMS = re.compile(
    r"\b(?:keep in mind|note that|remember|save this)\b", re.IGNORECASE
)
_MEMORY_CORRECT_TERMS = re.compile(
    r"\b(?:correct|correction|incorrect|not true|update that|wrong)\b", re.IGNORECASE
)
_MEMORY_FORGET_TERMS = re.compile(r"\b(?:delete|forget|remove)\b", re.IGNORECASE)
_STATUS_TERMS = re.compile(
    r"\b(?:connected|connection|connector|health|online|schema|status|sync|working)\b",
    re.IGNORECASE,
)

# When a truly multi-topic request matches more than eight tools, retain the
# tools that can safely complete an explicit action before broad read helpers.
_TOOL_PRIORITY = (
    "calendar_event_propose",
    "message_draft",
    "message_send_propose",
    "github_issue_propose",
    "reminder_set",
    "task_upsert",
    "task_complete",
    "remember",
    "memory_correct",
    "memory_feedback",
    "forget",
    "agenda_get",
    "brief_get",
    "memory_search",
    "profile_get",
    "connector_status",
    "system_status",
    "connector_records_get",
)


def select_hermes_tools(topic_text: str) -> frozenset[str]:
    """Choose the smallest useful MCP group for one request and its follow-up context."""

    selected: set[str] = set()
    if _STATUS_TERMS.search(topic_text):
        selected.update({"connector_status", "system_status"})

    if _TASK_TERMS.search(topic_text):
        selected.update({"agenda_get", "brief_get"})
        if _TASK_CREATE_TERMS.search(topic_text):
            selected.add("task_upsert")
        if _TASK_COMPLETE_TERMS.search(topic_text):
            selected.add("task_complete")
        if _REMINDER_TERMS.search(topic_text):
            selected.update({"reminder_set", "task_upsert"})

    if _CALENDAR_TERMS.search(topic_text):
        selected.update({"agenda_get", "brief_get", "connector_records_get"})
        if _CALENDAR_WRITE_TERMS.search(topic_text):
            selected.add("calendar_event_propose")

    if _MAIL_TERMS.search(topic_text):
        if _MAIL_DRAFT_TERMS.search(topic_text):
            selected.add("message_draft")
        if _MAIL_SEND_TERMS.search(topic_text):
            selected.add("message_send_propose")

    if _GITHUB_TERMS.search(topic_text) and _GITHUB_WRITE_TERMS.search(topic_text):
        selected.update({"github_issue_propose", "action_commit"})

    if _MEMORY_TERMS.search(topic_text):
        selected.update({"memory_search", "profile_get"})
        if _MEMORY_WRITE_TERMS.search(topic_text):
            selected.add("remember")
        if _MEMORY_CORRECT_TERMS.search(topic_text):
            selected.update({"memory_correct", "memory_feedback"})
        if _MEMORY_FORGET_TERMS.search(topic_text):
            selected.add("forget")

    ordered = [name for name in _TOOL_PRIORITY if name in selected]
    return frozenset(ordered[:MAX_HERMES_TOOLS_PER_TURN])
