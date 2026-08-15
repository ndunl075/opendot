"""What each connector is actually allowed to do, declared in one place.

Section 3's connector contract says every connector "declares read/write
capabilities, OAuth scopes, sensitivity, polling/webhook support, and rate
limits". Nothing declared any of it. `connector_health.py` answers *is it
working*; there was no answer at all to *what can it do* -- so the only way
to establish whether Canvas can write, or which Google scopes are actually
requested, or which connector stores `sensitive` data, was to read the
source of eight modules and infer it.

That question is a security question, not a curiosity. Section 8 gates
writes behind previews and approvals and filters retrieval by sensitivity;
both are much easier to reason about when the surface is inspectable rather
than implied.

The declarations below are transcribed from the code they describe, and
`tests/test_connector_capabilities.py` cross-checks them against it: a module
that defines an `Actions` class must declare that it writes, and every
connector that appears in `sync_state` must be declared at all. A declaration
that can silently drift from the code is worse than no declaration, because
it invites trust it has not earned.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Sensitivity = Literal["public", "personal", "sensitive", "secret"]
Transport = Literal["poll", "push", "local"]


class ConnectorCapability(BaseModel):
    """One connector's declared surface.

    ``writes`` means the connector can change something outside Alfred. It
    does *not* mean it can do so unattended: every write here goes through the
    propose/approve/commit path in section 8, which is why ``write_actions``
    lists the proposal tools rather than raw API calls.
    """

    connector: str
    summary: str
    reads: bool = True
    writes: bool = False
    write_actions: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    sensitivity: Sensitivity = "personal"
    transport: Transport = "poll"
    notes: str = ""


CONNECTOR_CAPABILITIES: tuple[ConnectorCapability, ...] = (
    ConnectorCapability(
        connector="google_calendar",
        summary="Current calendar events, and the one connector that can create one.",
        writes=True,
        write_actions=("calendar_event_propose",),
        scopes=(
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
        ),
        notes="Event creation recovers across a crash through a stable provider event ID.",
    ),
    ConnectorCapability(
        connector="google_calendar_catalog",
        summary="The list of calendars themselves, so a shared calendar can be named.",
        scopes=("https://www.googleapis.com/auth/calendar.calendarlist.readonly",),
    ),
    ConnectorCapability(
        connector="google_calendar_history",
        summary="Bounded past events, read once and reused for academic history.",
        scopes=("https://www.googleapis.com/auth/calendar.calendarlist.readonly",),
    ),
    ConnectorCapability(
        connector="gmail",
        summary="Unread mail, plus drafting and sending behind an approval.",
        writes=True,
        write_actions=("message_draft", "message_send_propose"),
        scopes=(
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
        ),
        notes=(
            "compose covers drafting and sending; there is no separate send scope. "
            "Sends recover through a stable RFC 2822 Message-ID rather than retrying blind."
        ),
    ),
    ConnectorCapability(
        connector="gmail_inbound",
        summary="Commands emailed to Alfred from one allowlisted sender.",
        scopes=("https://www.googleapis.com/auth/gmail.readonly",),
        notes="Read-only by construction: an inbound message can create work, never authorize it.",
    ),
    ConnectorCapability(
        connector="github",
        summary="Notifications, plus opening an issue or PR comment behind an approval.",
        writes=True,
        write_actions=("github_issue_propose",),
        scopes=("notifications (classic PAT)", "repo-scoped fine-grained PAT for writes"),
        notes="Writes recover through hidden exact body markers, so a retry cannot double-post.",
    ),
    ConnectorCapability(
        connector="canvas",
        summary="Upcoming and missing coursework via an institution-issued token.",
        scopes=("institution-issued Canvas personal token",),
        notes="Read-only. Stores assignments and missing-submission state, never grades or files.",
    ),
    ConnectorCapability(
        connector="canvas_ical",
        summary="Degraded read-only coursework when Canvas API tokens are disabled.",
        scopes=("private iCalendar feed URL (a bearer secret)",),
        notes=(
            "Bounded full snapshots with ETag/Last-Modified validators. The feed URL never "
            "reaches SQLite or the audit log. Not API parity: no grades, submissions, or To Do state."
        ),
    ),
    ConnectorCapability(
        connector="google_health",
        summary="Sleep, activity, and heart metrics from a wearable-linked account.",
        scopes=(
            "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
            "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
            "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
        ),
        sensitivity="sensitive",
        notes=(
            "The only connector storing `sensitive` data, so client scopes exclude it by default. "
            "Built but never exercised against a real wearable-linked account."
        ),
    ),
    ConnectorCapability(
        connector="telegram",
        summary="The owner's chat channel: intake, replies, and approval buttons.",
        writes=True,
        write_actions=("telegram delivery (outbox)",),
        notes=(
            "Writes only back to an explicitly paired chat, never to a third party. "
            "Long polling, so no inbound port is opened."
        ),
    ),
    ConnectorCapability(
        connector="slack",
        summary="A paired Slack channel over Socket Mode.",
        writes=True,
        write_actions=("slack delivery (outbox)",),
        transport="push",
        notes="Socket Mode, so no public webhook or tunnel. Never exercised against a real workspace.",
    ),
    ConnectorCapability(
        connector="obsidian_vault",
        summary="User-authored Markdown notes imported as confirmed memory.",
        transport="local",
        notes="Alfred never writes back to an imported file; projections go only to Generated/.",
    ),
    ConnectorCapability(
        connector="people",
        summary="Person entities derived from calendar identities already synced.",
        transport="local",
        notes="Reads Alfred's own tables only; reaches no provider and creates nothing confirmed.",
    ),
)

_BY_NAME = {capability.connector: capability for capability in CONNECTOR_CAPABILITIES}


def capability_for(connector: str) -> ConnectorCapability | None:
    """Return one connector's declaration, or None if it is undeclared."""
    return _BY_NAME.get(connector)


def writing_connectors() -> tuple[str, ...]:
    """Every connector that can change something outside Alfred."""
    return tuple(item.connector for item in CONNECTOR_CAPABILITIES if item.writes)


def sensitive_connectors() -> tuple[str, ...]:
    """Connectors storing data above the default `personal` tier."""
    return tuple(
        item.connector for item in CONNECTOR_CAPABILITIES if item.sensitivity in {"sensitive", "secret"}
    )
