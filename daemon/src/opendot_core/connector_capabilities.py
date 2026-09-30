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
    #: How much this connector asks of a provider per sync. Alfred does no
    #: quota accounting, so this describes the bound that keeps usage small
    #: -- the page size actually requested and anything capping a single
    #: sync -- rather than a limit Alfred enforces. On top of this, a failing
    #: connector backs off exponentially (30s doubling, capped at its own
    #: interval), so a dead provider is not retried every runner cycle.
    rate_limit: str = "one bounded read per sync interval"
    notes: str = ""


CONNECTOR_CAPABILITIES: tuple[ConnectorCapability, ...] = (
    ConnectorCapability(
        connector="google_calendar",
        rate_limit="one incremental sync-token read per calendar, per interval",
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
        rate_limit="one calendar-list read per interval",
        summary="The list of calendars themselves, so a shared calendar can be named.",
        scopes=("https://www.googleapis.com/auth/calendar.calendarlist.readonly",),
    ),
    ConnectorCapability(
        connector="google_calendar_history",
        rate_limit="one bounded window read, on its own longer interval",
        summary="Bounded past events, read once and reused for academic history.",
        scopes=("https://www.googleapis.com/auth/calendar.calendarlist.readonly",),
    ),
    ConnectorCapability(
        connector="gmail",
        rate_limit="maxResults 100 per page, bounded by the configured unread limit",
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
        rate_limit="one bounded unread query per interval, allowlisted sender only",
        summary="Commands emailed to Alfred from one allowlisted sender.",
        scopes=("https://www.googleapis.com/auth/gmail.readonly",),
        notes="Read-only by construction: an inbound message can create work, never authorize it.",
    ),
    ConnectorCapability(
        connector="github",
        rate_limit="per_page 50 notifications per sync",
        summary="Notifications, plus opening an issue or PR comment behind an approval.",
        writes=True,
        write_actions=("github_issue_propose",),
        scopes=("notifications (classic PAT)", "repo-scoped fine-grained PAT for writes"),
        notes="Writes recover through hidden exact body markers, so a retry cannot double-post.",
    ),
    ConnectorCapability(
        connector="canvas",
        rate_limit="one upcoming/missing query per interval",
        summary="Upcoming and missing coursework via an institution-issued token.",
        scopes=("institution-issued Canvas personal token",),
        notes="Read-only. Stores assignments and missing-submission state, never grades or files.",
    ),
    ConnectorCapability(
        connector="canvas_ical",
        rate_limit="one conditional GET per interval; ETag/Last-Modified usually make it a 304",
        summary="Degraded read-only coursework when Canvas API tokens are disabled.",
        scopes=("private iCalendar feed URL (a bearer secret)",),
        notes=(
            "Bounded full snapshots with ETag/Last-Modified validators. The feed URL never "
            "reaches SQLite or the audit log. Not API parity: no grades, submissions, or To Do state."
        ),
    ),
    ConnectorCapability(
        connector="composio",
        rate_limit="100k tool calls per UTC month on the free tier (hard cap); locally counted in tool_runs",
        summary="Overflow apps Composio hosts (Notion, Spotify, Linear, …), not first-party connectors.",
        writes=True,
        write_actions=("composio_execute",),
        scopes=("Composio API key in the OS keyring; connected-account tokens stay at Composio",),
        notes=(
            "Gmail, Calendar, GitHub, Slack, Telegram, and Fitbit stay first-party. "
            "Reads run now; writes preview and wait for Telegram approval. "
            "Do not point Hermes at Composio's hosted MCP URL — YOLO would auto-approve."
        ),
    ),
    ConnectorCapability(
        connector="google_health",
        rate_limit="one bounded lookback read per interval",
        summary="Sleep, activity, and heart metrics from a wearable-linked account.",
        scopes=(
            "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
            "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
            "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
        ),
        sensitivity="sensitive",
        notes=(
            "The only connector storing `sensitive` data, so client scopes exclude it by default. "
            "Opt in with `alfred google-auth --include-health`. Syncs steps, sleep sessions, and "
            "daily resting heart rate -- not sample-level BPM, which is too dense for the event log."
        ),
    ),
    ConnectorCapability(
        connector="telegram",
        rate_limit="10s long poll, 12s read ceiling, 1s retry after a failed poll",
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
        rate_limit="persistent Socket Mode connection; no polling",
        summary="A paired Slack channel over Socket Mode.",
        writes=True,
        write_actions=("slack delivery (outbox)",),
        transport="push",
        notes="Socket Mode, so no public webhook or tunnel. Never exercised against a real workspace.",
    ),
    ConnectorCapability(
        connector="obsidian_vault",
        rate_limit="local filesystem scan; no provider involved",
        summary="User-authored Markdown notes imported as confirmed memory.",
        transport="local",
        notes="Alfred never writes back to an imported file; projections go only to Generated/.",
    ),
    ConnectorCapability(
        connector="people",
        rate_limit="reads Alfred's own tables only; no provider involved",
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
