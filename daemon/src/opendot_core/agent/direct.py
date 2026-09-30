"""Direct code answers: narrow read-only questions answered without a model."""

from __future__ import annotations

import json
import re
from datetime import datetime

from ..db import Database
from .context import GITHUB_TERMS, INBOX_TERMS

_CALENDAR_READ_TERMS = re.compile(
    r"\b(?:calendar|agenda|schedule|appointments?|meetings?|events?)\b", re.IGNORECASE
)
_CALENDAR_WRITE_TERMS = re.compile(
    r"\b(?:add|book|cancel|create|delete|move|reschedule|set up)\b|\bschedule\s+(?:a|an|the)\b",
    re.IGNORECASE,
)
_NON_TODAY_CALENDAR_RANGE = re.compile(
    r"\b(?:tomorrow|yesterday|week|weekend|month|next|later|upcoming)\b", re.IGNORECASE
)
_CALENDAR_PROVENANCE_TERMS = re.compile(
    r"\b(?:added|created|creator|organized|organizer|owner|whose|who|which calendar)\b",
    re.IGNORECASE,
)

#: The connector whose freshness is reported for a direct calendar answer.
CALENDAR_CONNECTOR = "google_calendar"


def direct_answer(database: Database, request: str) -> str | None:
    """Answer narrow read-only questions locally when language adds no value.

    A request for today's calendar is structured data retrieval, not a
    reasoning task. Answering it in code removes model cold-start and provider
    failure from a common command while still leaving ambiguous, multi-topic,
    future-range, and write requests to the model. Returns None whenever the
    request is not exactly that narrow shape.
    """
    if not _CALENDAR_READ_TERMS.search(request):
        return None
    if INBOX_TERMS.search(request) or GITHUB_TERMS.search(request):
        return None
    if _CALENDAR_WRITE_TERMS.search(request) or _NON_TODAY_CALENDAR_RANGE.search(request):
        return None

    local_now = datetime.now().astimezone()
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT account, payload_json FROM connector_records
            WHERE connector = 'google_calendar'
              AND record_type = 'event'
              AND active = 1
            """
        ).fetchall()
        catalog_state = connection.execute(
            """
            SELECT last_success_at, last_error FROM sync_state
            WHERE connector = 'google_calendar_catalog' AND account = 'self'
            """
        ).fetchone()
        calendars = connection.execute(
            """
            SELECT payload_json FROM connector_records
            WHERE connector = 'google_calendar'
              AND account = 'self'
              AND record_type = 'calendar'
              AND active = 1
            """
        ).fetchall()
        calendar_states = connection.execute(
            """
            SELECT account, last_success_at, last_error FROM sync_state
            WHERE connector = 'google_calendar'
            """
        ).fetchall()

    if not catalog_state or not catalog_state["last_success_at"]:
        return (
            "I only have your primary calendar synced right now, so I can't reliably say "
            "whether your full Google Calendar is clear.\n\n"
            "want me to check the calendar connection?"
        )
    if catalog_state["last_error"]:
        return (
            "I couldn't verify your full Google Calendar list, so I can't answer that reliably yet.\n\n"
            "want me to check the calendar connection?"
        )

    expected_accounts = {
        "primary" if payload.get("primary") else str(payload.get("id"))
        for row in calendars
        if isinstance((payload := json.loads(row["payload_json"])), dict) and payload.get("id")
    }
    state_by_account = {str(row["account"]): row for row in calendar_states}
    incomplete = [
        account
        for account in expected_accounts
        if account not in state_by_account
        or not state_by_account[account]["last_success_at"]
        or state_by_account[account]["last_error"]
    ]
    calendar_labels: dict[str, str] = {}
    for row in calendars:
        payload = json.loads(row["payload_json"])
        calendar_id = str(payload.get("id") or "")
        label = str(payload.get("title") or calendar_id)
        if calendar_id:
            calendar_labels[calendar_id] = label
        if payload.get("primary"):
            calendar_labels["primary"] = label

    events: list[tuple[datetime | None, str, str, str | None]] = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        start_value = payload.get("start")
        if not isinstance(start_value, str) or not start_value:
            continue
        try:
            if "T" in start_value:
                start = datetime.fromisoformat(start_value.replace("Z", "+00:00"))
                if start.tzinfo is None:
                    start = start.replace(tzinfo=local_now.tzinfo)
                start = start.astimezone(local_now.tzinfo)
                event_date = start.date()
            else:
                start = None
                event_date = datetime.fromisoformat(start_value).date()
        except ValueError:
            continue
        if event_date == local_now.date():
            title = str(payload.get("title") or "Untitled calendar event")
            calendar_id = str(payload.get("calendar_id") or row["account"])
            calendar_label = calendar_labels.get(calendar_id, calendar_id)
            creator = payload.get("creator")
            added_by = None
            if isinstance(creator, dict):
                added_by = str(creator.get("displayName") or creator.get("email") or "") or None
            events.append((start, title, calendar_label, added_by))

    events.sort(
        key=lambda item: (
            item[0] is not None,
            item[0].timestamp() if item[0] is not None else 0.0,
            item[1].casefold(),
        )
    )
    if not expected_accounts:
        return (
            "My calendar coverage is incomplete right now, so I can't answer that reliably yet.\n\n"
            "want me to check the calendar connection?"
        )
    if not events and incomplete:
        return (
            "I don't see anything today on the calendars I could check, but my calendar coverage is incomplete.\n\n"
            "want me to check the missing calendar connection?"
        )
    if not events:
        return "your calendar is clear today.\n\nwant me to add anything?"

    lines = [f"today: {len(events)} event{'s' if len(events) != 1 else ''}"]
    show_provenance = bool(_CALENDAR_PROVENANCE_TERMS.search(request))
    show_added_by = bool(
        show_provenance
        and re.search(r"\bwho\b|\badded\b|\bcreated\b|\bcreator\b", request, re.IGNORECASE)
    )
    for start, title, calendar_label, added_by in events[:3]:
        when = "all day" if start is None else start.strftime("%I:%M %p").lstrip("0").lower()
        detail = ""
        if show_provenance:
            detail = f" ({calendar_label}"
            if show_added_by and added_by:
                detail += f", added by {added_by}"
            detail += ")"
        lines.append(f"{when}: {title}{detail}")
    if len(events) > 3:
        lines.append(f"plus {len(events) - 3} more")
    if incomplete:
        lines.append("one calendar couldn't be checked, so this may be incomplete.")
    return "\n".join(lines) + "\n\nwant me to add or change anything?"


def calendar_context_freshness(database: Database) -> str | None:
    """Latest successful calendar sync time, for the context trace of a direct answer."""
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT MAX(last_success_at) AS latest
            FROM sync_state
            WHERE connector = 'google_calendar'
            """
        ).fetchone()
    return str(row["latest"]) if row and row["latest"] else None
