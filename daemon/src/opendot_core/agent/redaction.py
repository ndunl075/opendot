"""Prompt redaction that scrubs stored content but not the owner's own request."""

from __future__ import annotations

from ..models import Redactor

#: The final line of each prompt shape, after which everything is the owner's
#: own words for this turn.
_CURRENT_REQUEST_MARKERS = ("\ncurrent request: ", "\ncurrent message: ")


def redact_except_current_request(prompt: str, redactor: Redactor) -> str:
    """Scrub everything OpenDot stored, and nothing the owner just typed.

    The context pack above the marker is synced third-party content; the
    request below it is the sentence the owner wrote this turn, including any
    recipient they named. Redacting both made sending an email impossible:
    "send an email to my mom (mom@example.com)" arrived as "[REDACTED:email]",
    so the assistant asked for the address, and the reply was scrubbed the
    same way.

    Falls back to redacting everything if the marker is absent, so an
    unexpected prompt shape fails closed rather than leaking the pack.
    """
    for marker in _CURRENT_REQUEST_MARKERS:
        head, found, request = prompt.rpartition(marker)
        if found:
            return redactor.redact(head) + found + request
    return redactor.redact(prompt)
