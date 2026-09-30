"""Conservative matching for the same academic item arriving through two connectors."""

from __future__ import annotations

import re
from datetime import UTC, datetime

_TITLE_WORDS = re.compile(r"[\w]+", re.UNICODE)


def academic_item_signature(title: object, timestamp: object) -> tuple[str, int] | None:
    """Return a strict title-and-minute signature, or ``None`` for incomplete data.

    Canvas's native feed and its Google Calendar subscription normally carry
    the same title and instant. Requiring both prevents similarly named work
    on different dates from being collapsed.
    """

    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(timestamp, str) or not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    normalized_title = " ".join(_TITLE_WORDS.findall(title.casefold()))
    if not normalized_title:
        return None
    minute = int(parsed.astimezone(UTC).timestamp() // 60)
    return normalized_title, minute
