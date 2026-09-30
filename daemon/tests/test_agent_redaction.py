"""Prompt redaction: scrub stored content, never the owner's own request."""

from __future__ import annotations

from opendot_core.agent.redaction import redact_except_current_request
from opendot_core.models import Redactor


def test_pii_is_redacted_at_the_prompt_boundary() -> None:
    redacted = redact_except_current_request(
        "email me at owner@example.com or call 513-555-1212", Redactor()
    )

    assert "owner@example.com" not in redacted
    assert "513-555-1212" not in redacted
    assert "[REDACTED:email]" in redacted


def test_the_owners_current_request_keeps_its_recipient() -> None:
    """Redacting the request made sending an email impossible: the address the
    owner typed arrived as [REDACTED:email] and the assistant asked for it."""
    prompt = (
        "context\n<opendot_context>{\"from\":\"vendor@example.com\"}</opendot_context>\n"
        "current request: send an email to my mom (mom@example.com)"
    )

    redacted = redact_except_current_request(prompt, Redactor())

    assert "vendor@example.com" not in redacted
    assert "current request: send an email to my mom (mom@example.com)" in redacted


def test_the_casual_prompt_shape_is_split_on_its_own_marker() -> None:
    prompt = "pack vendor@example.com\ncurrent message: text mom@example.com"

    redacted = redact_except_current_request(prompt, Redactor())

    assert "vendor@example.com" not in redacted
    assert redacted.endswith("current message: text mom@example.com")


def test_a_prompt_without_a_marker_is_redacted_everywhere() -> None:
    """An unexpected shape fails closed rather than leaking the pack."""
    redacted = redact_except_current_request("reach owner@example.com", Redactor())

    assert "owner@example.com" not in redacted
