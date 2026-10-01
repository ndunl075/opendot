"""Morning brief routine (ARCHITECTURE.md 7.1: plain code; one optional cheap/low wording pass).

The brief is Alfred's deterministic ``BriefingService`` output. The opt-in "friendlier wording" switch
adds exactly one ``summarize`` pass over the finished text. If that pass fails or is paused, the plain
brief is delivered unchanged: a missing model never costs the user their brief.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..briefing import BriefingService, MorningBrief
from .base import Outcome, Routine, RoutineContext, RoutineState
from .model_pass import clean_field, untrusted_block
from .settings import MORNING_BRIEF_KEY, MorningBriefSettings

LATE_NOTE_AFTER = timedelta(minutes=5)

REWORD_INSTRUCTION = (
    "Rewrite the owner's morning briefing below as a short, warm, plain-text message. Keep every fact, "
    "date, time and link exactly as given. Do not add, guess or invent anything. Reply with the message only."
)


def brief_has_content(brief: MorningBrief) -> bool:
    return any(
        (
            brief.overdue,
            brief.due_today,
            brief.upcoming,
            brief.no_due_date,
            brief.calendar_today,
            brief.github_notifications,
            brief.important_dates,
            brief.conflicts,
        )
    )


def reword(context: RoutineContext, plain: str, *, job_type: str = "summarize") -> tuple[str, Outcome]:
    """One cheap/low pass over finished text. Returns the text to deliver and an Outcome carrying the facts."""
    outcome = Outcome(status="delivered", text=plain)
    result = context.model.run(
        REWORD_INSTRUCTION,
        untrusted_block(clean_field(line, 400) for line in plain.splitlines() if line.strip()),
        job_type=job_type,
    )
    outcome.model_called = result.called
    outcome.credits = result.credits
    if result.text:
        outcome.used_model = True
        outcome.text = result.text
    else:
        outcome.fallback_reason = result.reason
    return outcome.text or plain, outcome


class MorningBriefRoutine(Routine):
    name = "morning_brief"
    settings_key = MORNING_BRIEF_KEY
    settings_cls = MorningBriefSettings
    kind = "daily"

    def run(
        self,
        context: RoutineContext,
        settings: Any,
        state: RoutineState,
        now: datetime,
        scheduled_at: datetime | None,
    ) -> Outcome:
        late = scheduled_at if scheduled_at is not None and now - scheduled_at > LATE_NOTE_AFTER else None
        brief = BriefingService(context.database).morning_brief(
            now, scheduled_at=late, timezone_name=settings.timezone
        )
        plain = brief.render()
        if not settings.friendlier_wording or not brief_has_content(brief):
            return Outcome(status="delivered", text=plain)
        _, outcome = reword(context, plain)
        return outcome
