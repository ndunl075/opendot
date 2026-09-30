"""Human wall-clock helpers shared by schedules, reports, and the brief."""

from __future__ import annotations

from datetime import time, timedelta


def parse_hhmm(value: str) -> time:
    """Parse a stored or configured ``HH:MM`` local time."""
    hour_text, minute_text = value.strip().split(":", maxsplit=1)
    return time(int(hour_text), int(minute_text))


def format_duration(value: timedelta) -> str:
    """Render a span the way a person says it: ``7h 30m``, ``7h``, ``45m``."""
    total_minutes = int(value.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
