"""Inbox triage routine (ARCHITECTURE.md 7.1 "Sorting incoming items" and 8.2 rules 1 and 7).

Code first: the routine does nothing unless the Gmail sync has stored unread messages it has not triaged
before. Plain code then drops bulk mail, merges threads and duplicates, and ranks what is left (sender
importance from the people graph, threads awaiting a reply, mentions of the owner's open tasks and
dates, high-signal wording). Only then does ONE cheap/low ``sort`` pass see the new items, and only as
numbered headers and short snippets, never a message body. Email is untrusted (section 10): it goes in as
escaped data in the changing tail, and the pass has no tools, so triage cannot send, draft, label or
delete. If the pass fails or is paused, the code-ranked list is delivered instead.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parseaddr
from typing import Any

from ..agent.mail import HIGH_SIGNAL_MAIL, low_priority_mail
from ..important_dates import ImportantDateStore
from ..memory_graph import MemoryGraph
from ..threads import ThreadService
from .base import MAX_SEEN_IDS, Outcome, Routine, RoutineContext, RoutineState
from .model_pass import clean_field, untrusted_block
from .settings import INBOX_TRIAGE_KEY, InboxTriageSettings

MAX_CANDIDATES = 12
SNIPPET_CHARS = 200
SUBJECT_CHARS = 120
FROM_CHARS = 80
REASON_CHARS = 100

INSTRUCTION = (
    "You are helping the owner sort new email. Each numbered item is one message: sender, subject and a "
    "short snippet. For each item that is worth the owner's attention now, reply with one line in the form "
    "`<number>: <reason in at most 10 words>`. Leave out items that can wait or are bulk. If none are worth "
    "it, reply with exactly `none`. Reply with nothing else."
)

_LINE = re.compile(r"^\s*(\d{1,3})\s*[:.)\-]\s*(.*)$")
_STOPWORDS = frozenset(
    "the and for with from that this have your about into over need needs call email send make "
    "check review buy pay get set pick book ask".split()
)
_WORD = re.compile(r"[a-z0-9]{4,}")


@dataclass
class Candidate:
    message_id: str
    sender: str
    subject: str
    snippet: str
    score: int = 0
    why: list[str] = field(default_factory=list)
    count: int = 1


def _words(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if word not in _STOPWORDS}


def _date_forms(now: datetime) -> set[str]:
    forms: set[str] = set()
    for day in (now, now + timedelta(days=1)):
        forms.add(day.date().isoformat())
        forms.add(f"{day.strftime('%b')} {day.day}".lower())
        forms.add(f"{day.strftime('%B')} {day.day}".lower())
    return forms


class _Ranker:
    def __init__(self, context: RoutineContext, now: datetime) -> None:
        database = context.database
        self.now = now
        self.graph = MemoryGraph(database)
        report = ThreadService(database).awaiting_reply(now=now)
        self.awaiting = {item.thread_id for item in report.awaiting_reply}
        with database.connect() as connection:
            rows = connection.execute("SELECT title FROM tasks WHERE state = 'open' ORDER BY created_at DESC LIMIT 60")
            self.task_titles = [str(row["title"]) for row in rows.fetchall()]
        try:
            self.date_labels = [item.label.lower() for item in ImportantDateStore.list_all(database)]
        except Exception:
            self.date_labels = []
        self.date_forms = _date_forms(now)

    def sender_importance(self, sender: str) -> tuple[int, str]:
        name, address = parseaddr(sender)
        for key in (address, name):
            if not key:
                continue
            try:
                entity = self.graph.resolve_entity_by_name(key)
            except Exception:
                entity = None
            if entity is not None and entity.entity_type == "person":
                return (3, "someone you know") if entity.confirmed else (2, "a known contact")
        return 0, ""

    def score(self, candidate: Candidate, payload: dict[str, Any]) -> None:
        text = f"{candidate.subject} {candidate.snippet}".lower()
        points, why = self.sender_importance(candidate.sender)
        if points:
            candidate.score += points
            candidate.why.append(why)
        thread_id = payload.get("thread_id")
        if isinstance(thread_id, str) and thread_id in self.awaiting:
            candidate.score += 1
            candidate.why.append("may need a reply")
        if HIGH_SIGNAL_MAIL.search(text):
            candidate.score += 3
            candidate.why.append("urgent wording")
        text_words = _words(text)
        for title in self.task_titles:
            wanted = _words(title)
            if title.lower() in text or (wanted and len(wanted & text_words) >= min(2, len(wanted))):
                candidate.score += 2
                candidate.why.append("mentions one of your tasks")
                break
        if any(label and label in text for label in self.date_labels) or any(form in text for form in self.date_forms):
            candidate.score += 1
            candidate.why.append("mentions a date that is close")
        labels = payload.get("label_ids")
        if isinstance(labels, list) and "CATEGORY_PRIMARY" in labels:
            candidate.score += 1


def unread_messages(context: RoutineContext) -> dict[str, dict[str, Any]]:
    with context.database.connect() as connection:
        rows = connection.execute(
            "SELECT record_id, payload_json FROM connector_records WHERE connector = 'gmail' AND account = 'self' "
            "AND record_type = 'unread_message' AND active = 1 ORDER BY observed_at DESC, record_id"
        ).fetchall()
    return {str(row["record_id"]): json.loads(row["payload_json"]) for row in rows}


def rank_new_messages(
    context: RoutineContext, new: dict[str, dict[str, Any]], now: datetime
) -> list[Candidate]:
    """Plain code: drop bulk, merge threads and duplicates, score, sort. No model, no bodies."""
    ranker = _Ranker(context, now)
    merged: dict[str, Candidate] = {}
    for message_id, payload in new.items():
        if low_priority_mail(payload):
            continue
        sender = clean_field(payload.get("from"), FROM_CHARS)
        subject = clean_field(payload.get("subject") or "(no subject)", SUBJECT_CHARS)
        thread = payload.get("thread_id")
        key = f"thread:{thread}" if isinstance(thread, str) and thread else f"dup:{sender.lower()}|{subject.lower()}"
        existing = merged.get(key)
        if existing is not None:
            existing.count += 1
            continue
        candidate = Candidate(message_id, sender, subject, clean_field(payload.get("snippet"), SNIPPET_CHARS))
        ranker.score(candidate, payload)
        merged[key] = candidate
    ordered = sorted(merged.values(), key=lambda item: (-item.score, item.subject.lower(), item.message_id))
    return ordered[:MAX_CANDIDATES]


def parse_picks(reply: str, size: int) -> list[tuple[int, str]] | None:
    """``[(item number, reason)]`` from the model's reply, or None when it cannot be read."""
    text = reply.strip()
    if text.lower().strip(" .`") == "none":
        return []
    picks: list[tuple[int, str]] = []
    for line in text.splitlines():
        match = _LINE.match(line.strip().strip("`"))
        if not match:
            continue
        number = int(match.group(1))
        if 1 <= number <= size and all(number != seen for seen, _ in picks):
            picks.append((number, clean_field(match.group(2), REASON_CHARS)))
    return picks or None


def render(items: list[tuple[Candidate, str]], *, total_new: int, sorted_by_model: bool) -> str:
    header = f"Inbox: {len(items)} of {total_new} new message(s) look worth a look"
    lines = [header + ("." if sorted_by_model else " (ranked by simple rules).")]
    for candidate, reason in items:
        who = f" - {candidate.sender}" if candidate.sender else ""
        more = f" (+{candidate.count - 1} more in the thread)" if candidate.count > 1 else ""
        why = f" ({reason})" if reason else ""
        lines.append(f"- {candidate.subject}{who}{more}{why}")
    return "\n".join(lines)


class InboxTriageRoutine(Routine):
    name = "inbox_triage"
    settings_key = INBOX_TRIAGE_KEY
    settings_cls = InboxTriageSettings
    kind = "interval"

    def run(
        self,
        context: RoutineContext,
        settings: Any,
        state: RoutineState,
        now: datetime,
        scheduled_at: datetime | None,
    ) -> Outcome:
        unread = unread_messages(context)
        seen = set(state.seen_ids)
        new = {message_id: payload for message_id, payload in unread.items() if message_id not in seen}
        # Remember only what is still unread, so the list stays small and a message that returns is new again.
        kept = [message_id for message_id in state.seen_ids if message_id in unread]
        remembered = (kept + [message_id for message_id in new if message_id not in seen])[-MAX_SEEN_IDS:]
        if not new:
            return Outcome(status="nothing_new", seen_ids=remembered)
        ranked = rank_new_messages(context, new, now)
        digest = hashlib.sha256("\n".join(sorted(new)).encode()).hexdigest()[:16]
        if not ranked:  # all bulk: nothing for the model to decide and nothing worth the user's time
            return Outcome(status="nothing_new", seen_ids=remembered, details={"new": len(new), "bulk_only": True})
        data = untrusted_block(
            f"{index}. from: {item.sender} | subject: {item.subject} | snippet: {item.snippet}"
            for index, item in enumerate(ranked, start=1)
        )
        result = context.model.run(INSTRUCTION, data, job_type="sort")
        picks = parse_picks(result.text, len(ranked)) if result.text else None
        outcome = Outcome(
            status="delivered",
            model_called=result.called,
            credits=result.credits,
            seen_ids=remembered,
            dedupe=digest,
            details={"new": len(new), "candidates": len(ranked)},
        )
        if picks is not None:
            outcome.used_model = True
            if not picks:
                outcome.status = "nothing_new"  # the model looked and nothing is worth interrupting for
                return outcome
            chosen = [(ranked[number - 1], reason) for number, reason in picks[: settings.max_items]]
            outcome.text = render(chosen, total_new=len(new), sorted_by_model=True)
            return outcome
        outcome.fallback_reason = result.reason or "the model's reply could not be read"
        fallback = [(item, ", ".join(dict.fromkeys(item.why[:2]))) for item in ranked[: settings.max_items]]
        outcome.text = render(fallback, total_new=len(new), sorted_by_model=False)
        return outcome
