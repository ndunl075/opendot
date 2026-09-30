"""Deterministic mail ranking: which unread messages matter, which are bulk."""

from __future__ import annotations

import re
from typing import Any

_LOW_VALUE_GMAIL_LABELS = frozenset(
    {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}
)
HIGH_SIGNAL_MAIL = re.compile(
    r"\b(?:action required|security alert|sign[ -]?in|password|verification|verify|"
    r"payment failed|past due|overdue|invoice|receipt|paused?|suspended?|expires?|"
    r"deadline|due (?:today|tomorrow|this week)|direct question)\b",
    re.IGNORECASE,
)
BULK_MAIL = re.compile(
    r"\b(?:unsubscribe|newsletter|daily digest|weekly digest|marketing preferences|"
    r"sale ends|sitewide|free ship(?:ping)?|new notifications?|"
    r"people viewed your profile|league-winning|shop now)\b|"
    r"(?:\$\d+|\d+%)\s+off\b",
    re.IGNORECASE,
)


def _text_and_labels(payload: dict[str, Any]) -> tuple[str, set[str]]:
    text = " ".join(str(payload.get(key) or "") for key in ("subject", "from", "snippet"))
    raw_labels = payload.get("label_ids")
    labels = (
        {label for label in raw_labels if isinstance(label, str)}
        if isinstance(raw_labels, list)
        else set()
    )
    return text, labels


def low_priority_mail(payload: dict[str, Any]) -> bool:
    """Use Gmail's own category first, with a conservative legacy fallback.

    Existing rows created before label capture may not have ``label_ids`` yet,
    so unmistakable newsletter language is still suppressed. High-signal
    security, billing, and deadline language wins even if Gmail categorized a
    message as bulk. List-Unsubscribe is stronger than CATEGORY_* alone --
    live mail showed most newsletters labeled CATEGORY_PERSONAL.
    """
    text, labels = _text_and_labels(payload)
    if labels & _LOW_VALUE_GMAIL_LABELS:
        return True
    list_unsubscribe = payload.get("list_unsubscribe")
    if isinstance(list_unsubscribe, str) and list_unsubscribe.strip():
        if HIGH_SIGNAL_MAIL.search(text):
            return False
        return True
    if HIGH_SIGNAL_MAIL.search(text):
        return False
    return bool(BULK_MAIL.search(text))


def mail_rank(payload: dict[str, Any]) -> tuple[int, int, str]:
    """Put consequential and Primary mail ahead of neutral unread messages."""
    text, labels = _text_and_labels(payload)
    return (
        0 if HIGH_SIGNAL_MAIL.search(text) else 1,
        0 if "CATEGORY_PRIMARY" in labels else 1,
        str(payload.get("subject") or "").lower(),
    )
