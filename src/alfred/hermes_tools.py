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
_DAY_PLANNING_TERMS = re.compile(
    r"\b(?:what should i (?:do|work on)|plan my day|what(?:'s| is) on my plate|how does my day look)\b",
    re.IGNORECASE,
)
_SOCIAL_GREETING = re.compile(
    r"^\s*(?:yo+|hey+|hi+|sup|what(?:'s| is) up|how are you|how(?:'s| is) it going|wyd)[?!.\s]*$",
    re.IGNORECASE,
)
_EXPLICIT_WORK_TERMS = re.compile(
    r"\b(?:agenda|assignment|calendar|canvas|class|connector|course|deadline|due|"
    r"email|gmail|github|health|inbox|issue|mail|meeting|memory|note|pull request|"
    r"remind|reminder|repo|schedule|search the web|slack|task|to-?do|web search|workout)\b",
    re.IGNORECASE,
)

# Facts about the outside world that Alfred cannot know on its own. These are
# not "work" in the connector sense -- no Alfred tool answers them -- but they
# are emphatically not small talk either: the casual lane runs a small model
# with reasoning disabled and no tools, so routing one of these there produces
# a confident guess or a timeout, never an answer. "who's playing tomorrow in
# the cinci open" is the case that exposed this; it needs a web search, and
# the casual lane cannot make one.
_EXTERNAL_LOOKUP_TERMS = re.compile(
    r"\b(?:"
    r"look (?:it|this|that|them)? ?up|look up|google|find out|"
    r"search|searching|"
    r"weather|forecast|temperature|"
    r"score|scores|standings|bracket|seeded?|lineup|"
    r"playing|plays|match(?:es|up)?|tournament|championships?|"
    r"news|headlines?|"
    r"stock|ticker|"
    r"who won|who’s winning|who's winning|how much (?:is|are|does)"
    r")\b",
    re.IGNORECASE,
)

# A request phrased as a question, used only to decide whether a substantive
# message continues a lookup the previous turn set up.
_QUESTION_SHAPE = re.compile(
    r"(?:\?\s*$)|^\s*(?:who|what|when|where|which|whose|how many|how much|is|are|does|do|did|can you)\b",
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

    if _TASK_TERMS.search(topic_text) or _DAY_PLANNING_TERMS.search(topic_text):
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


def is_external_lookup(request: str) -> bool:
    """True when answering needs the outside world rather than Alfred's data.

    Used to skip work that cannot possibly help: no local memory vector,
    calendar row, or inbox record answers "who's playing tomorrow", and the
    embedding round-trip alone cost 7.2 seconds of a 103-second turn.
    """
    return bool(_EXTERNAL_LOOKUP_TERMS.search(request))


def is_casual_conversation(request: str, *, recent_topic_text: str = "") -> bool:
    """Route ordinary chat away from agentic reasoning and connector tools.

    The casual lane is a small model with reasoning disabled and no tools, so
    "casual" has to mean *answerable with no outside information*, not merely
    "doesn't mention a connector". Anything needing a fact Alfred cannot know
    on its own belongs in the capable lane even though no Alfred tool serves
    it -- Hermes's own web search does.
    """
    if _SOCIAL_GREETING.fullmatch(request):
        return True
    if _EXPLICIT_WORK_TERMS.search(request) or _DAY_PLANNING_TERMS.search(request):
        return False
    if _EXTERNAL_LOOKUP_TERMS.search(request):
        return False
    # Short follow-ups inherit a recent work topic ("why?", "yeah do that"),
    # while a new substantive message starts its own conversational turn.
    if len(request.split()) <= 12 and _EXPLICIT_WORK_TERMS.search(recent_topic_text):
        return False
    # A question asked right after a lookup was set up is the answer to
    # "what do you want me to search?" -- the naming of the thing to look up
    # arrives in its own message and would otherwise read as a fresh topic
    # with no lookup words of its own.
    if _QUESTION_SHAPE.search(request) and _EXTERNAL_LOOKUP_TERMS.search(recent_topic_text):
        return False
    return True
