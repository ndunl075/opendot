"""Built-in routines (M4 task 4.5): morning brief, inbox triage, weekly review.

Code first, cheap by default (ARCHITECTURE.md 7.1 and 8.2): each routine is plain code, and the one
optional model pass goes through the agent loop so budgets, the plan-limit pause and the kill switch apply.
"""

from .base import Outcome, Routine, RoutineContext, RoutineState
from .inbox_triage import InboxTriageRoutine
from .morning_brief import MorningBriefRoutine
from .scheduler import RoutineResult, RoutineScheduler, RoutineStatus, default_routines
from .settings import (
    INBOX_TRIAGE_KEY,
    MORNING_BRIEF_KEY,
    WEEKLY_REVIEW_KEY,
    InboxTriageSettings,
    MorningBriefSettings,
    WeeklyReviewSettings,
)
from .weekly_review import WeeklyReviewRoutine

__all__ = [
    "INBOX_TRIAGE_KEY",
    "MORNING_BRIEF_KEY",
    "WEEKLY_REVIEW_KEY",
    "InboxTriageRoutine",
    "InboxTriageSettings",
    "MorningBriefRoutine",
    "MorningBriefSettings",
    "Outcome",
    "Routine",
    "RoutineContext",
    "RoutineResult",
    "RoutineScheduler",
    "RoutineState",
    "RoutineStatus",
    "WeeklyReviewRoutine",
    "WeeklyReviewSettings",
    "default_routines",
]
