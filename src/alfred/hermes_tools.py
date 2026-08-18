"""Deterministic per-turn MCP tool selection for Alfred's Hermes subprocess."""

from __future__ import annotations

import re

HERMES_MCP_TOOL_FILTER_ENV = "ALFRED_HERMES_MCP_TOOLS"
HERMES_TELEGRAM_CHAT_ID_ENV = "ALFRED_TELEGRAM_CHAT_ID"
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
_EMAIL_ADDRESS = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_MAIL_DRAFT_TERMS = re.compile(r"\b(?:compose|draft|reply|respond|write)\b", re.IGNORECASE)
_MAIL_SEND_TERMS = re.compile(
    r"\b(?:send(?:\s+(?:it|this|that|to|an?\s+email|email))?|email them|message them)\b",
    re.IGNORECASE,
)
# Distinct from a plain inbox check: the bridge already prefetches unread mail,
# so threads_awaiting_reply is only offered when the user asks for that report.
_AWAITING_REPLY_TERMS = re.compile(
    r"\b(?:"
    r"awaiting(?:\s+my)?\s+reply|need(?:s)?\s+(?:a\s+)?reply|"
    r"waiting\s+(?:on|for)\s+(?:my\s+)?reply|threads?\s+awaiting|"
    r"what(?:'s| is)?\s+waiting|who(?:'s| is)?\s+waiting|"
    # How the question is actually asked. Every phrasing below reached the
    # casual lane with no tools and was answered from nothing: the model has
    # no synced mail, so it invented a plausible reply about the owner's
    # inbox. repl(?:y|ies) rather than repl\b -- there is no word boundary
    # between "l" and "y", so \b never matched "reply" at all.
    r"who\s+has(?:n.?t| not)\s+(?:replied|responded|answered|got back)|"
    r"(?:what|which)\s+(?:emails?|messages?|threads?)\s+am\s+i\s+waiting\s+on|"
    r"owe\s+(?:a\s+|an\s+)?(?:repl(?:y|ies)|answer|response)|"
    r"went\s+quiet|go(?:ne)?\s+quiet|ghosted|"
    r"unanswered|no\s+(?:repl(?:y|ies)|response|answer)"
    r")\b",
    re.IGNORECASE,
)

#: "Is Alfred actually syncing?", narrowly enough to decide a lane. Naming
#: the machinery explicitly, unlike _STATUS_TERMS, whose bare "status" and
#: "working" belong to ordinary speech as much as to connectors.
_CONNECTOR_HEALTH_TERMS = re.compile(
    r"\b(?:"
    r"connector|connectors|"
    r"(?:are|is)\s+(?:all\s+)?(?:my\s+)?\w+\s+(?:still\s+)?(?:connected|syncing)|"
    r"last\s+sync|syncing|out of sync|"
    r"connector\s+(?:status|health)|system\s+status"
    r")\b",
    re.IGNORECASE,
)

#: Free time in the calendar. `availability_get` shipped without any routing
#: at all, so "when am i free thursday" was answered by a model with no
#: calendar -- confidently, and with nothing anywhere recording that it had
#: guessed. Kept apart from the calendar *write* vocabulary: this asks what
#: the calendar already says rather than asking to put something on it.
_AVAILABILITY_TERMS = re.compile(
    r"(?:"
    r"(?:when|what times?)\s+(?:am|are)\s+(?:i|we)\s+free|"
    r"(?:am|are)\s+(?:i|we)\s+(?:free|busy|available)|"
    r"free\s+(?:time|slots?)|open\s+(?:slots?|time)|"
    r"availabilit(?:y|ies)|"
    r"do\s+(?:i|we)\s+have\s+(?:any\s+|some\s+)?time|"
    r"gaps?\s+in\s+(?:my|the)\s+(?:calendar|day|schedule|week)|"
    r"find\s+(?:me\s+)?(?:an?|some)\s+(?:hour|time|slot)|"
    r"when\s+(?:can|could)\s+(?:i|we)\s+(?:meet|fit|do)"
    r")",
    re.IGNORECASE,
)
_GITHUB_TERMS = re.compile(
    r"\b(?:github|issue|pull request|repo|repository)\b", re.IGNORECASE
)
_GITHUB_WRITE_TERMS = re.compile(
    r"\b(?:create|file|open|report)\b.*\bissue\b|\bissue\b.*\b(?:create|file|open|report)\b",
    re.IGNORECASE,
)
# Overflow apps Alfred does not own first-party. Kept off Gmail/Calendar/GitHub
# on purpose: those have dedicated connectors and must not route here.
_COMPOSIO_TERMS = re.compile(
    r"\b(?:"
    r"composio|notion|spotify|linear|jira|asana|trello|todoist|"
    r"discord|zoom|figma|airtable|hubspot|salesforce|linkedin|"
    r"instagram|youtube|reddit|dropbox|box.com|onedrive"
    r")\b",
    re.IGNORECASE,
)
_COMPOSIO_CONNECT_TERMS = re.compile(
    r"\b(?:connect|sign in|sign into|link|authorize|auth)\b",
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
    # sync(?:ing|ed|s)? rather than sync: \bsync\b matches none of the forms
    # people actually use ("is gmail still syncing", "when did it last sync"),
    # the same word-boundary trap that made \breply\b miss "replied".
    r"\b(?:connected|connection|connector|online|schema|status|sync(?:ing|ed|s)?|working)\b",
    re.IGNORECASE,
)
# Wearable / Google Health reads. Kept separate from _STATUS_TERMS so "connector
# health" still maps to connector_status while "how did I sleep" maps here.
_HEALTH_TERMS = re.compile(
    r"\b(?:"
    r"health|steps|step count|sleep|slept|resting heart|heart rate|bpm|fitbit|wearable|"
    r"activity(?:\s+data)?|workout|last night(?:'s)?\s+sleep"
    r")\b",
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
    r"airtable|asana|composio|course|deadline|discord|due|email|figma|gmail|github|health|hubspot|inbox|issue|jira|linear|"
    r"linkedin|lock[\s-]?in|mail|meeting|notion|"
    r"gratitude|journal|memory|mood|nag|note|pull request|remind(?:er|ing|s)?|repo|salesforce|schedule|"
    r"search the web|slack|sleep|spotify|steps|task|todoist|trello|"
    r"to-?do|wake(?:\s+me)?\s*up|wake-up|web search|workout|zoom)\b",
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
# Same pairing as notify intent: a future time plus an ordinary action verb
# is "do this later", not "do this now". Without these, "do this at 8" and
# "send that tomorrow night" never offered a scheduling tool.
_DELAYED_ACTION_TERMS = re.compile(
    r"\b(?:do|send|email|draft|run|finish|complete)\b",
    re.IGNORECASE,
)
# Asking what happens at a time is not a request to run something then.
# Narrower than _QUESTION_SHAPE so "do this at 8" (imperative do) still
# schedules.
_TIME_QUESTION = re.compile(
    r"^\s*(?:what(?:'s|s| is)|who(?:'s|s| is)|when(?:'s|s| is))\b",
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
_MOOD_TERMS = re.compile(
    r"\b(?:mood|feeling|feelings|how am i|how was my day|mood check(?:-|\s)?in)\b",
    re.IGNORECASE,
)
_GRATITUDE_TERMS = re.compile(
    r"\b(?:gratitude|grateful|thankful|thanks for|three things|gratitude journal)\b",
    re.IGNORECASE,
)
_JOURNAL_TERMS = re.compile(
    r"\b(?:journal|mood trend|mood history|gratitude entries)\b",
    re.IGNORECASE,
)


def wants_scheduling(text: str) -> bool:
    """True when the request is "do something later and report back".

    Either an explicit reminder word, a daily routine (wake/bedtime/lock-in),
    or a future time paired with an action or an intent to be told the result.
    Both halves are required for the paired form so a plain question about a
    time ("what's at 3pm?") is not mistaken for a request to schedule
    something. Calendar booking uses the time as the event's start, not as
    when to run a job, so "book a meeting at 3" stays a calendar write.
    """
    if _REMINDER_TERMS.search(text) or _DAILY_ROUTINE_TERMS.search(text):
        return True
    if not _FUTURE_TIME_TERMS.search(text):
        return False
    if _TIME_QUESTION.search(text):
        return False
    if _CALENDAR_TERMS.search(text) and _CALENDAR_WRITE_TERMS.search(text):
        return False
    return bool(_NOTIFY_INTENT_TERMS.search(text) or _DELAYED_ACTION_TERMS.search(text))


# When a truly multi-topic request matches more than eight tools, retain the
# tools that can safely complete an explicit action before broad read helpers.
_TOOL_PRIORITY = (
    "calendar_event_propose",
    "task_schedule",
    "message_draft",
    "message_send_propose",
    "github_issue_propose",
    "composio_execute",
    "composio_connect",
    "important_date_set",
    "mood_record",
    "gratitude_record",
    "journal_get",
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
    "composio_search",
    "composio_status",
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

    if _MOOD_TERMS.search(topic_text):
        selected.update({"mood_record", "journal_get"})
    if _GRATITUDE_TERMS.search(topic_text):
        selected.update({"gratitude_record", "journal_get"})
    if _JOURNAL_TERMS.search(topic_text):
        selected.add("journal_get")

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

    # Top level, not nested under the mail vocabulary. "who hasn't replied to
    # me" names no mail word at all -- and _MAIL_TERMS' own `\breply\b` does
    # not match "replied" either, so the outer gate rejected the question
    # before the inner test could ever see it. It is a question about who owes
    # whom, which happens to be answered from mail.
    if _AWAITING_REPLY_TERMS.search(topic_text):
        selected.add("threads_awaiting_reply")

    if _AVAILABILITY_TERMS.search(topic_text):
        selected.add("availability_get")

    if _MAIL_TERMS.search(topic_text) or _EMAIL_ADDRESS.search(topic_text):
        if _MAIL_DRAFT_TERMS.search(topic_text):
            selected.add("message_draft")
        if _MAIL_SEND_TERMS.search(topic_text):
            selected.add("message_send_propose")

    if _HEALTH_TERMS.search(topic_text):
        # brief_get folds last night's sleep when synced; connector_records_get
        # reaches google_health snapshots when the user wants detail.
        selected.update({"brief_get", "connector_records_get"})

    if _GITHUB_TERMS.search(topic_text) and _GITHUB_WRITE_TERMS.search(topic_text):
        # Deliberately only the proposal. action_commit used to be added here
        # and was kept out of the model's hands solely by being absent from
        # _TOOL_PRIORITY, so a security property section 7 states outright --
        # "the conversational model never receives action_commit in its
        # per-turn tool list" -- rested on an ordering table one well-meaning
        # edit could undo. It is not selected at all now, so the trim is no
        # longer load-bearing. This mattered more than it looked: until the
        # turn handshake landed, the per-turn filter never reached the MCP
        # server, so every tool really was exposed regardless.
        selected.add("github_issue_propose")
    elif _PR_WATCH_TERMS.search(topic_text) or (
        _GITHUB_TERMS.search(topic_text) and re.search(r"\bopen\b", topic_text, re.IGNORECASE)
    ):
        selected.add("pull_requests_get")

    if _COMPOSIO_TERMS.search(topic_text):
        selected.update({"composio_search", "composio_execute"})
        if _COMPOSIO_CONNECT_TERMS.search(topic_text):
            selected.add("composio_connect")
        if _STATUS_TERMS.search(topic_text):
            selected.add("composio_status")

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


def wants_mail_write(text: str) -> bool:
    """True when this turn is drafting or sending mail, not reading the inbox."""
    mailish = bool(_MAIL_TERMS.search(text) or _EMAIL_ADDRESS.search(text))
    if _MAIL_SEND_TERMS.search(text) and (
        mailish or re.search(r"\bsend\s+(?:it|this|that|to)\b", text, re.IGNORECASE)
    ):
        return True
    return mailish and bool(_MAIL_DRAFT_TERMS.search(text))


def is_fresh_mail_write(text: str) -> bool:
    """True for a new send/draft that does not continue a previous letter."""
    if not wants_mail_write(text):
        return False
    if _EMAIL_ADDRESS.search(text):
        return False
    return not bool(
        re.search(r"\b(?:it|that|this|those|same)\b", text, re.IGNORECASE)
    )


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
    if _MOOD_TERMS.search(request) or _GRATITUDE_TERMS.search(request) or _JOURNAL_TERMS.search(request):
        return False
    # These four read synced local data, and the casual lane carries none of
    # it. Each one sounds like small talk -- "am i free thursday", "any prs
    # waiting on me" -- which is exactly why they slipped through: a question
    # phrased casually was routed casually and answered by a model with no
    # calendar, no GitHub snapshot, and no mail. Nothing failed; the answer
    # was simply invented, and no error was recorded anywhere.
    if _AVAILABILITY_TERMS.search(request):
        return False
    if _AWAITING_REPLY_TERMS.search(request):
        return False
    if _PR_WATCH_TERMS.search(request):
        return False
    # Deliberately narrower than _STATUS_TERMS, which is fine for choosing a
    # tool but far too broad to decide a lane: it matches "status", "working",
    # "health", and "online" as bare words, so guarding on it sent "how should
    # you write status updates?" -- a question about voice -- to the tool lane.
    # A lane guard has to name the machinery explicitly.
    if _CONNECTOR_HEALTH_TERMS.search(request):
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
