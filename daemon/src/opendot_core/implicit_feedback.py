"""Notice a bad answer instead of asking for a vote on every good one.

Every successful reply used to carry `helpful` / `missing context` /
`wrong context` buttons. The mechanism was fine and the data was useful; the
demand was the problem. Rating a personal secretary after each answer is work,
so the buttons were mostly ignored, and a signal that only arrives when
someone bothers is a signal that mostly does not arrive.

The correction was already in the chat. "you missed the one from sam", "that's
the wrong week", "thanks, perfect" -- people say what they think of an answer
in the next message whether or not a keyboard is attached. This module reads
those sentences, plus one thing the owner cannot see at all: whether the
context Alfred answered *from* was actually current.

Two detectors, deliberately different in kind:

``classify_reply`` reads the owner's next message and returns one of the same
three outcomes the buttons recorded. Rules, not a model call: this runs inside
Telegram intake's write transaction, where a model round trip is not an
option, and a rule that fires on "you forgot" is also a rule that can be read,
tested, and argued with -- which matters more here than recall, because a
wrong verdict quietly reorders what Alfred shows next.

``detect_context_gap`` grades the pack a reply was built from. A source that
has never synced, or last synced a day ago, means the answer was written from
context that was already missing something. Nobody has to notice that, and
nobody realistically would.

Precision beats recall throughout, and every rule below is written to fail
closed: an unrecognized message records nothing, which is exactly what the
buttons did when they went untapped. Only the rule's name is ever stored, so
this stays as content-free as the table it writes to.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

#: Where a verdict came from. Kept in the row so a metric can separate what the
#: owner said from what Alfred inferred about itself, and so ranking can prefer
#: the stronger signal when one response collects more than one.
SIGNAL_BUTTON = "button"
SIGNAL_REPLY = "reply"
SIGNAL_COVERAGE = "coverage"

#: How long after an answer a message can still be read as a reaction to it.
#: Long enough for someone to look something up and come back, short enough
#: that tomorrow's unrelated question is not scored against last night's reply.
REPLY_WINDOW_SECONDS = 30 * 60

#: A connector that last synced longer ago than this was not describing the
#: present when the answer quoted it. Generous on purpose: the run loop syncs
#: far more often, so crossing a full day means the connector is broken or
#: unauthorized, not merely between passes.
STALE_CONTEXT_SECONDS = 24 * 60 * 60


class InferredFeedback(BaseModel):
    """One verdict plus the named rule that produced it, never the text."""

    outcome: str
    rule: str


Rule = tuple[str, re.Pattern[str]]


#: The answer was wrong, not merely incomplete. Each pattern needs an explicit
#: statement of wrongness; none of them fire on a bare "no", because Alfred's
#: own replies end with offers ("want me to add anything?") and "no i'm good"
#: declines an offer rather than disputing a fact.
_WRONG_CONTEXT_RULES: tuple[Rule, ...] = (
    (
        "denial",
        re.compile(
            r"\b(?:that|this|it)(?:'s|s| is| was)?\s+"
            r"(?:wrong|incorrect|false|outdated|stale|"
            r"not\s+(?:right|correct|true|it|what|the))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "blamed",
        re.compile(
            r"\byou(?:'re|re| are)\s+wrong\b|"
            r"\byou\s+got\s+(?:that|it|this)\s+wrong\b|"
            r"\byou\s+(?:made\s+that\s+up|mixed\s+(?:that|it|them)\s+up)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "contradicted",
        re.compile(
            r"\bno,?\s+(?:it(?:'s|s| is)?\s+not|it\s+isn'?t|it\s+wasn'?t|"
            r"that(?:'s|s| is)?\s+not|that\s+isn'?t)\b",
            re.IGNORECASE,
        ),
    ),
    # "wrong" alone is a trap: "what's wrong with CI" is a question, not a
    # complaint. Naming the thing that was wrong is what makes it a verdict.
    (
        "wrong_target",
        re.compile(
            r"\bwrong\s+(?:one|person|email|thread|message|link|day|date|time|"
            r"week|month|class|course|repo|issue|answer|thing)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "misread",
        re.compile(
            r"\b(?:not|isn'?t)\s+what\s+i\s+(?:asked|meant|said|wanted)\b|"
            r"\bi\s+(?:never|didn'?t)\s+(?:say|said|tell|mention)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "doubted",
        re.compile(
            r"\bi\s+don'?t\s+think\s+(?:that'?s|thats|that\s+is|it'?s|its|this\s+is)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "unhelpful",
        re.compile(
            r"\b(?:that|this|it)(?:'s|s| is| was)?\s+(?:not\s+helpful|unhelpful|useless)\b|"
            r"\bthat\s+didn'?t\s+(?:help|answer)\b",
            re.IGNORECASE,
        ),
    ),
)

#: The answer was right as far as it went and left something out. Every rule
#: names an omission by Alfred; a bare "what about tomorrow" is excluded on
#: purpose, since that is usually the next question rather than a complaint
#: about the last answer.
_MISSING_CONTEXT_RULES: tuple[Rule, ...] = (
    (
        "omitted",
        re.compile(
            r"\byou\s+(?:missed|forgot|skipped|overlooked|left\s+out)\b|"
            r"\byou\s+(?:didn'?t|did\s+not|never)\s+"
            r"(?:mention|include|list|say|show|see|catch|check|look)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "incomplete",
        re.compile(
            r"\b(?:that|this|it)(?:'s|s| is)?\s+not\s+(?:all|everything|the\s+whole)\b|"
            r"\bthat\s+can'?t\s+be\s+(?:all|it)\b|"
            r"\byou\s+only\s+(?:showed|listed|found|mentioned|said|got)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "more_exists",
        re.compile(
            r"\bthere(?:'s|s| is| are)\s+(?:also|another|more|others)\b|"
            r"\bwhat\s+happened\s+to\s+the\b",
            re.IGNORECASE,
        ),
    ),
    (
        "recheck",
        re.compile(
            r"\b(?:check|look|search)\s+again\b|"
            r"\bdid\s+you\s+(?:check|look|search|see)\b|"
            r"\bare\s+you\s+sure\s+you\s+(?:checked|looked|searched)\b",
            re.IGNORECASE,
        ),
    ),
)

#: The answer landed. Praise is the weakest of the three signals and the
#: easiest to misread, so it is checked last and blocked outright by the
#: declines below.
_HELPFUL_RULES: tuple[Rule, ...] = (
    (
        "gratitude",
        re.compile(
            r"\b(?:thanks|thank\s+you|thx|tysm|ty)\b|"
            r"\bappreciate\s+(?:it|that|you)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "praise",
        re.compile(
            # A bare praise word has to open the message and stand alone or be
            # punctuated, or it is describing something other than the answer:
            # "beautiful day today" and "what exactly did she say" are not
            # verdicts, and both would otherwise match.
            r"^(?:perfect|awesome|excellent|amazing|beautiful|great|nice|sweet|sick)"
            r"(?:\b\s*[.!,]*$|\s*[,.!])|"
            r"\b(?:that'?s|thats)\s+(?:perfect|awesome|excellent|amazing|great|exactly)\b|"
            r"\bexactly\s+(?:what|right)\b|"
            r"\b(?:nailed\s+it|spot\s+on|well\s+done)\b|"
            r"\b(?:great|nice|good)\s+(?:work|call|catch|looking\s+out)\b|"
            r"\blove\s+(?:it|that|this)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "confirmed",
        re.compile(
            r"\b(?:that|this)\s+(?:helps|helped|worked)\b|"
            r"\b(?:that|this)\s+(?:is|was)\s+(?:helpful|perfect|great)\b",
            re.IGNORECASE,
        ),
    ),
)

#: Politeness that is not a verdict on the last answer. "no thanks" contains
#: "thanks", "thanks in advance" is paying for the *next* thing, and "not
#: great" is the opposite of the word it contains. These only suppress a
#: helpful verdict; a correction in the same message still wins, because
#: corrections are checked first.
_NOT_PRAISE = re.compile(
    r"\bno,?\s+thanks\b|\bnah\b|\bnever\s?mind\b|\bnvm\b|"
    r"\bthanks\s+in\s+advance\b|"
    r"\bnot\s+(?:great|perfect|helpful)\b",
    re.IGNORECASE,
)

#: Strongest first. A message that both thanks and corrects ("thanks but you
#: missed sam's") is a correction: the praise is manners, the miss is the
#: part worth acting on.
_RULE_ORDER: tuple[tuple[str, tuple[Rule, ...]], ...] = (
    ("wrong_context", _WRONG_CONTEXT_RULES),
    ("missing_context", _MISSING_CONTEXT_RULES),
    ("helpful", _HELPFUL_RULES),
)


def classify_reply(text: str) -> InferredFeedback | None:
    """Read one follow-up message as a verdict on the answer before it.

    Returns ``None`` for anything that is not unmistakably a reaction, which
    is most messages. That is the intended behavior, not a gap: recording a
    guess on every turn would bury the votes that mean something.
    """
    message = (text or "").strip()
    if not message:
        return None
    for outcome, rules in _RULE_ORDER:
        # Reached only after the two correction passes found nothing, so a
        # declined offer stops here rather than being counted as praise.
        if outcome == "helpful" and _NOT_PRAISE.search(message):
            return None
        for rule, pattern in rules:
            if pattern.search(message):
                return InferredFeedback(outcome=outcome, rule=rule)
    return None


def detect_context_gap(
    *,
    sources: list[str],
    freshness: dict[str, str | None],
    now: datetime | None = None,
) -> InferredFeedback | None:
    """Flag an answer that was built from context nobody could call current.

    Freshness is each connector's own last successful sync, recorded in the
    same trace that names the sources. A source in the pack with no successful
    sync behind it, or one a full day stale, means the reply described a state
    of the world Alfred had already lost track of. That is missing context by
    definition, and unlike a wrong date it leaves no trace the owner could
    notice and complain about.
    """
    if not sources:
        return None
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(seconds=STALE_CONTEXT_SECONDS)
    # Sorted so a pack with two bad sources always reports the same one; only
    # one verdict per response is stored, and an arbitrary pick would make the
    # rule name flap between runs.
    for source in sorted(sources):
        if source not in freshness:
            continue
        last_success = freshness.get(source)
        if not last_success:
            return InferredFeedback(outcome="missing_context", rule=f"unsynced:{source}")
        try:
            synced_at = datetime.fromisoformat(str(last_success))
        except ValueError:
            return InferredFeedback(outcome="missing_context", rule=f"unsynced:{source}")
        if synced_at.tzinfo is None:
            synced_at = synced_at.replace(tzinfo=UTC)
        if synced_at < cutoff:
            return InferredFeedback(outcome="missing_context", rule=f"stale:{source}")
    return None


def find_reaction_target(
    connection: sqlite3.Connection,
    *,
    chat_id: int,
    user_id: int,
    now: datetime,
) -> str | None:
    """Return the answer a message arriving now would be reacting to.

    The most recent answered turn in this chat, inside the reply window. Only
    turns this exact paired sender asked are eligible, matching the pairing
    check the buttons enforced on every tap.

    Attribution is to the *last* answer, not the last substantive one. After
    "thanks" is itself answered, a later "you missed sam's" attaches to that
    short exchange rather than to the inbox rundown two messages up. Chasing
    the intended target would mean guessing at what a sentence refers to,
    which is the thing this module refuses to do; the verdict is still
    recorded, and a turn that packed no sources simply has nothing to reorder.
    """
    cutoff = (now - timedelta(seconds=REPLY_WINDOW_SECONDS)).isoformat()
    rows = connection.execute(
        """
        SELECT c.response_update_id, e.metadata_json
        FROM response_context c
        JOIN events e
          ON e.source = 'telegram' AND e.external_id = c.response_update_id
        WHERE c.created_at >= ?
        ORDER BY c.created_at DESC, c.rowid DESC
        LIMIT 10
        """,
        (cutoff,),
    ).fetchall()
    for row in rows:
        # Filtered in Python rather than SQL so this does not depend on the
        # JSON1 extension being present in every SQLite build.
        metadata = json.loads(row["metadata_json"])
        if metadata.get("chat_id") != chat_id or metadata.get("user_id") != user_id:
            continue
        if not metadata.get("agent_deferred"):
            continue
        return str(row["response_update_id"])
    return None
