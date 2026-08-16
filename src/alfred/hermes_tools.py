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
_REMINDER_TERMS = re.compile(r"\bremind(?:er|ing|s)?\b", re.IGNORECASE)
_NAG_TERMS = re.compile(
    r"\b(?:"
    r"keep reminding(?:\s+me)?|nag(?:\s+me)?|"
    r"until\s+(?:I(?:\s+(?:do|finish|complete|done))?|done)"
    r")\b",
    re.IGNORECASE,
)
_CALENDAR_TERMS = re.compile(
    r"\b(?:agenda|appointment|availability|available|calendar|event|free|meeting|schedule)\b",
    re.IGNORECASE,
)
_CALENDAR_WRITE_TERMS = re.compile(
    r"\b(?:add|book|create|move|reschedule|schedule|set up)\b", re.IGNORECASE
)
_MAIL_TERMS = re.compile(r"\b(?:email|gmail|inbox|mail|message|reply)\b", re.IGNORECASE)
_MAIL_DRAFT_TERMS = re.compile(r"\b(?:compose|draft|reply|respond|write)\b", re.IGNORECASE)
_MAIL_SEND_TERMS = re.compile(r"\b(?:send|email them|message them)\b", re.IGNORECASE)
# Distinct from a plain inbox check: the bridge already prefetches unread mail,
# so threads_awaiting_reply is only offered when the user asks for that report.
_AWAITING_REPLY_TERMS = re.compile(
    r"\b(?:"
    r"awaiting(?:\s+my)?\s+reply|need(?:s)?\s+(?:a\s+)?reply|"
    r"waiting\s+(?:on|for)\s+(?:my\s+)?reply|threads?\s+awaiting|"
    r"what(?:'s| is)?\s+waiting|who(?:'s| is)?\s+waiting"
    r")\b",
    re.IGNORECASE,
)
_GITHUB_TERMS = re.compile(
    r"\b(?:github|issue|pull request|repo|repository)\b", re.IGNORECASE
)
_GITHUB_WRITE_TERMS = re.compile(
    r"\b(?:create|file|open|report)\b.*\bissue\b|\bissue\b.*\b(?:create|file|open|report)\b",
    re.IGNORECASE,
)
_PR_WATCH_TERMS = re.compile(
    r"\b(?:"
    r"open pull requests?|pull requests? awaiting|my pull requests?|my prs?|"
    r"prs?(?:\s+open|\s+waiting|\s+need|\s+awaiting)?|"
    r"review(?:s)?(?: requested)?|needs? (?:my )?review|waiting (?:on|for) (?:my )?review"
    r")\b",
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
    r"\b(?:agenda|anniversary|assignment|bedtime|birthday|calendar|canvas|class|connector|"
    r"course|deadline|due|email|gmail|github|health|inbox|issue|lock[\s-]?in|mail|meeting|"
    r"memory|nag|note|pull request|remind(?:er|ing|s)?|repo|schedule|search the web|slack|task|"
    r"to-?do|wake(?:\s+me)?\s*up|wake-up|web search|workout)\b",
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

# "Do this later and tell me." Only the word "remind" used to count, so
# "check again at 3 pm and text me" -- the same request in ordinary phrasing --
# matched nothing, offered the model no Alfred scheduling tool, and sent it to
# Hermes's own cron instead. That is a second job store Alfred does not run, so
# the job sat there and never fired. Section 2 makes Alfred the sole owner of
# schedules precisely to stop the two drifting apart.
_FUTURE_TIME_TERMS = re.compile(
    r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b"
    r"|\bin\s+\d+\s*(?:min(?:ute)?s?|hours?|hrs?|days?)\b"
    r"|\b(?:later|tonight|tomorrow|afterwards?)\b"
    r"|\bthis (?:morning|afternoon|evening)\b"
    r"|\b(?:next|by)\s+(?:week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
# Paired with a future time, any of these means "go do this then". The verbs
# are listed bare on purpose: an earlier version required "check again/back/it"
# and so missed "check at 3 pm who's playing", which is the same request with a
# different preposition. Being generous here is nearly free -- offering
# task_schedule costs one slot of eight and the model still decides -- while
# missing it is fatal, because with no scheduling tool at all the model either
# says it cannot schedule or invents a cron Alfred never runs.
_NOTIFY_INTENT_TERMS = re.compile(
    r"\b(?:text|message|ping|dm)\s+me\b"
    r"|\b(?:let me know|tell me|hit me up|follow up|get back to me)\b"
    r"|\b(?:check|look|see|find|watch|recheck)\b"
    r"|\bremind(?:er|ing)?\b",
    re.IGNORECASE,
)

# Fixed local-time routines: wake-up, bedtime, study lock-in. These are daily
# reminders, not agent tasks -- the text already exists. Matching them here
# keeps the capable lane and reminder_set available even when the message
# never says "remind".
_DAILY_ROUTINE_TERMS = re.compile(
    r"\b(?:"
    r"wake(?:\s+me)?\s*up|wake-up"
    r"|bedtime|bed\s+time|go to (?:bed|sleep)"
    r"|(?:study\s+)?lock[\s-]?in"
    r"|every\s+(?:day|morning|night|evening)"
    r"|each\s+(?:day|morning|night|evening)"
    r"|daily\s+reminder"
    r")\b",
    re.IGNORECASE,
)

_IMPORTANT_DATE_TERMS = re.compile(
    r"\b(?:"
    r"birthday|birthdays|anniversary|anniversaries|"
    r"important date|important dates|"
    r"turns \d+"
    r")\b",
    re.IGNORECASE,
)


def wants_scheduling(text: str) -> bool:
    """True when the request is "do something later and report back".

    Either an explicit reminder word, a daily routine (wake/bedtime/lock-in),
    or a future time paired with an intent to be told the result. Both halves
    are required for the paired form so a plain question about a time
    ("what's at 3pm?") is not mistaken for a request to schedule something.
    """
    if _REMINDER_TERMS.search(text) or _DAILY_ROUTINE_TERMS.search(text):
        return True
    return bool(_FUTURE_TIME_TERMS.search(text) and _NOTIFY_INTENT_TERMS.search(text))


# When a truly multi-topic request matches more than eight tools, retain the
# tools that can safely complete an explicit action before broad read helpers.
_TOOL_PRIORITY = (
    "calendar_event_propose",
    "task_schedule",
    "message_draft",
    "message_send_propose",
    "github_issue_propose",
    "important_date_set",
    "reminder_set",
    "nag_until_done",
    "task_upsert",
    "task_complete",
    "remember",
    "memory_correct",
    "memory_feedback",
    "forget",
    "important_dates_get",
    "threads_awaiting_reply",
    "availability_get",
    "pull_requests_get",
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

    # Top level, not nested under task phrasing: "check again at 3 and text me"
    # is a scheduling request without ever mentioning a task or a reminder.
    if wants_scheduling(topic_text):
        # task_schedule runs the work and reports back; reminder_set only
        # delivers text. Both are offered because "remind me to X" and "do X
        # and tell me" are different requests the model has to tell apart.
        # Daily routines (wake-up, bedtime, study lock-in) use reminder_set
        # with daily=true -- the message already exists.
        selected.update({"task_schedule", "reminder_set", "task_upsert"})

    if _DAILY_ROUTINE_TERMS.search(topic_text):
        selected.update({"reminder_set", "task_upsert"})

    if _IMPORTANT_DATE_TERMS.search(topic_text):
        selected.update({"important_date_set", "important_dates_get", "brief_get"})

    if _NAG_TERMS.search(topic_text):
        selected.update({"nag_until_done", "task_upsert", "task_complete"})

    if _TASK_TERMS.search(topic_text) or _DAY_PLANNING_TERMS.search(topic_text):
        selected.update({"agenda_get", "brief_get"})
        if _TASK_CREATE_TERMS.search(topic_text):
            selected.add("task_upsert")
        if _TASK_COMPLETE_TERMS.search(topic_text):
            selected.add("task_complete")
        if _REMINDER_TERMS.search(topic_text):
            selected.update({"reminder_set", "task_upsert"})

    if _CALENDAR_TERMS.search(topic_text):
        selected.update({"agenda_get", "brief_get", "connector_records_get", "availability_get"})
        if _CALENDAR_WRITE_TERMS.search(topic_text):
            selected.add("calendar_event_propose")

    if _MAIL_TERMS.search(topic_text):
        if _AWAITING_REPLY_TERMS.search(topic_text):
            selected.add("threads_awaiting_reply")
        if _MAIL_DRAFT_TERMS.search(topic_text):
            selected.add("message_draft")
        if _MAIL_SEND_TERMS.search(topic_text):
            selected.add("message_send_propose")

    if _GITHUB_TERMS.search(topic_text) and _GITHUB_WRITE_TERMS.search(topic_text):
        selected.update({"github_issue_propose", "action_commit"})
    elif _PR_WATCH_TERMS.search(topic_text) or (
        _GITHUB_TERMS.search(topic_text) and re.search(r"\bopen\b", topic_text, re.IGNORECASE)
    ):
        selected.add("pull_requests_get")

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
    # Scheduling needs a tool, and the casual lane has none.
    if wants_scheduling(request):
        return False
    if _IMPORTANT_DATE_TERMS.search(request):
        return False
    # Short follow-ups inherit a recent work topic ("why?", "yeah do that"),
    # while a new substantive message starts its own conversational turn.
    if len(request.split()) <= 12 and _EXPLICIT_WORK_TERMS.search(recent_topic_text):
        return False
    # The same inheritance for a lookup topic, and deliberately not limited to
    # question-shaped follow-ups. "for this season" narrows the previous
    # question without asking a new one, and routing it to the casual lane --
    # which has no web search -- left a small model to improvise an answer it
    # had no way to look up. A short message after a lookup is almost always
    # refining that lookup, not starting small talk.
    if len(request.split()) <= 12 and _EXTERNAL_LOOKUP_TERMS.search(recent_topic_text):
        return False
    if _QUESTION_SHAPE.search(request) and _EXTERNAL_LOOKUP_TERMS.search(recent_topic_text):
        return False
    return True
