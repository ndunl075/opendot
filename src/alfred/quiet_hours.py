"""Hold proactive (job-backed) deliveries during a local quiet window.

Interactive replies (Hermes / gateway outbox rows with no ``job_id``) still
deliver so a late-night chat is not silence. Reminders, briefs, and nags stay
``pending`` until the window ends.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator


def _parse_hhmm(value: str) -> time:
    hour_text, minute_text = value.strip().split(":", maxsplit=1)
    return time(int(hour_text), int(minute_text))


class QuietHours(BaseModel):
    """Local wall-clock quiet window. Disabled when start or end is unset."""

    start: time | None = None
    end: time | None = None
    timezone: str = Field(default="UTC")

    @model_validator(mode="after")
    def _both_or_neither(self) -> "QuietHours":
        if (self.start is None) ^ (self.end is None):
            raise ValueError("quiet hours require both start and end (HH:MM), or neither")
        if self.start is not None and self.end is not None and self.start == self.end:
            raise ValueError("quiet hours start and end must differ")
        # Validate IANA name early so misconfig fails at boot, not at delivery.
        ZoneInfo(self.timezone)
        return self

    @classmethod
    def disabled(cls) -> "QuietHours":
        return cls()

    @classmethod
    def from_environment(cls) -> "QuietHours":
        """Read ``ALFRED_QUIET_HOURS_{START,END,TIMEZONE}``; unset means disabled."""
        start_raw = os.environ.get("ALFRED_QUIET_HOURS_START", "").strip()
        end_raw = os.environ.get("ALFRED_QUIET_HOURS_END", "").strip()
        timezone = os.environ.get("ALFRED_QUIET_HOURS_TIMEZONE", "UTC").strip() or "UTC"
        if not start_raw and not end_raw:
            return cls.disabled()
        return cls(
            start=_parse_hhmm(start_raw) if start_raw else None,
            end=_parse_hhmm(end_raw) if end_raw else None,
            timezone=timezone,
        )

    def is_active(self, now: datetime | None = None) -> bool:
        """True when ``now`` falls inside the configured local window."""
        if self.start is None or self.end is None:
            return False
        local = (now or datetime.now(UTC)).astimezone(ZoneInfo(self.timezone)).time()
        # Overnight window (e.g. 22:00–07:00): active if after start or before end.
        if self.start > self.end:
            return local >= self.start or local < self.end
        return self.start <= local < self.end

    def holds_job_deliveries(self, now: datetime | None = None) -> bool:
        """Whether job-backed outbox rows should stay pending."""
        return self.is_active(now)
