"""Settings for the built-in routines, stored through ``SettingsStore`` (one key and one model each).

Every routine is off until the user turns it on and picks when it runs. Nothing here is a secret.
"""

from __future__ import annotations

from datetime import time
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator

from ..wall_clock import parse_hhmm

MORNING_BRIEF_KEY = "routine_morning_brief"
INBOX_TRIAGE_KEY = "routine_inbox_triage"
WEEKLY_REVIEW_KEY = "routine_weekly_review"

DEFAULT_DESTINATION = "desktop:owner"
"""Where a routine delivers unless the user picks another ``channel:recipient`` (desktop by default)."""

HHMM = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class _RoutineSettings(BaseModel):
    enabled: bool = False
    destination: str = DEFAULT_DESTINATION
    timezone: str = "UTC"

    @field_validator("destination")
    @classmethod
    def _channel_recipient(cls, value: str) -> str:
        if not value.strip() or ":" not in value:
            raise ValueError("destination must be a non-empty channel:recipient value")
        return value

    @field_validator("timezone")
    @classmethod
    def _iana_name(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError, OSError) as error:
            raise ValueError(f"unknown timezone {value!r}") from error
        return value


class MorningBriefSettings(_RoutineSettings):
    time: HHMM = "07:30"
    friendlier_wording: bool = False
    """Opt-in: one cheap, low-effort model pass rewords the finished brief (ARCHITECTURE.md 7.1)."""


class WeeklyReviewSettings(_RoutineSettings):
    weekday: int = Field(default=6, ge=0, le=6)
    """0 is Monday; the default 6 is Sunday."""
    time: HHMM = "18:00"
    friendlier_wording: bool = False


class InboxTriageSettings(_RoutineSettings):
    check_every_minutes: int = Field(default=30, ge=5, le=1440)
    max_items: int = Field(default=5, ge=1, le=10)


def parse_time(value: str) -> time:
    return parse_hhmm(value)
