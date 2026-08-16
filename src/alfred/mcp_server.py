"""Alfred's MCP surface: stdio plus loopback-only Streamable HTTP.

Section 7's transport policy: stdio for local Hermes/Claude/Cursor, and
Streamable HTTP on ``/mcp`` for other local/private clients -- bound to
``127.0.0.1`` only, with every remote request authenticated. Full OAuth
2.1/RFC 9728 is reserved for public remote access, a separate, larger
undertaking this module does not attempt; while loopback-only, a single
shared bearer token is enough to satisfy "authenticate every remote
request" without standing up an authorization server.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Sequence, cast
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .briefing import BriefingService
from .config import Settings
from .connector_health import connector_health
from .db import Database
from .events import EventStore
from .action_executor import ActionExecutor
from .availability import AvailabilityService
from .gmail import GmailActions, GmailSendActions
from .github import GitHubActions
from .google_calendar import GoogleCalendarActions
from .hermes_tools import HERMES_MCP_TOOL_FILTER_ENV
from .http_auth import BearerAuthMiddleware as _BearerAuthMiddleware
from .http_auth import bearer_token as _bearer_token
from .http_auth import generate_token as generate_http_token
from .important_dates import ImportantDateStore
from .journal import JournalStore
from .memory_graph import GraphError, MemoryActions, MemoryGraph, Sensitivity
from .memory_learning import MemoryFeedbackStore
from .models import Redactor
from .policy import ApprovalService, PolicyError, PolicyStore
from .nags import NagStore
from .pull_requests import PullRequestService
from .reminders import ReminderStore
from .github import GitHubClient
from .scheduled_tasks import ScheduledTaskStore
from .secret_store import SystemKeyringSecretStore
from .tasks import UNSET, TaskStore
from .threads import ThreadService
from .workflow_learning import WorkflowObservationStore, current_workflow_turn_id

ALLOWED_SENSITIVITIES: frozenset[str] = frozenset({"public", "personal", "sensitive", "secret"})
MCP_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "system_status",
        "agenda_get",
        "memory_search",
        "profile_get",
        "remember",
        "memory_correct",
        "memory_feedback",
        "forget",
        "calendar_event_propose",
        "message_draft",
        "message_send_propose",
        "github_issue_propose",
        "action_commit",
        "brief_get",
        "connector_status",
        "connector_records_get",
        "task_upsert",
        "task_complete",
        "reminder_set",
        "nag_until_done",
        "task_schedule",
        "important_date_set",
        "important_dates_get",
        "threads_awaiting_reply",
        "availability_get",
        "pull_requests_get",
        "mood_record",
        "gratitude_record",
        "journal_get",
    }
)


def create_server(
    database_path: Path | str | None = None,
    *,
    client_id: str = "local-mcp",
    tool_filter: frozenset[str] | None = None,
) -> FastMCP:
    """Create Alfred's MCP server: local memory reads/writes and connector status.

    Every tool is gated by PolicyStore, so an unregistered or narrowly scoped
    client gets nothing by default. Consequential actions are two calls, not
    one: forget(), calendar_event_propose(), message_draft(), and
    message_send_propose() only
    preview; action_commit() performs whatever a prior call previewed, once
    a fresh approval token is presented. Decision 8 requires this for
    deleting data, calendar writes, and sending a message alike
    ("strong-confirm" / "preview + confirm"), and there is deliberately no
    MCP tool to grant that approval -- letting the same automated client
    both propose and approve its own action would defeat the point, so a
    human has to grant it through a channel outside the MCP client's own
    reach (e.g. CLI's approval-approve). message_draft creates a Gmail draft;
    message_send_propose is a separate explicit send preview, and neither
    action can execute without a fresh human approval token.
    """
    settings = Settings.from_environment(Path(database_path) if database_path else None)
    database = Database(settings.database_path)
    policy = PolicyStore(database)
    approvals = ApprovalService(database)
    workflow_observations = WorkflowObservationStore(database)
    server = FastMCP("Alfred")

    def alfred_tool(
        *,
        read_only: bool = False,
        destructive: bool = False,
        idempotent: bool = False,
        open_world: bool = False,
    ):
        """Register a tool, annotate it, and retain privacy-safe workflow structure.

        The annotations are the point of the keyword arguments. An MCP client
        decides from these hints whether a call can run unprompted or has to
        be shown to a human first, so leaving them off -- as this server did
        until now -- silently downgrades every tool to "unknown", and a client
        that would have paused on a destructive call has nothing to pause on.

        They are hints about intent, not enforcement: Alfred's own policy
        checks, previews, and approval tokens remain the actual boundary. A
        client that ignores annotations entirely still cannot commit an action
        without a one-time token.
        """

        def decorate(function):
            signature = inspect.signature(function)

            @wraps(function)
            def observed(*args, **kwargs):
                result = function(*args, **kwargs)
                turn_id = current_workflow_turn_id()
                if turn_id:
                    try:
                        bound = signature.bind(*args, **kwargs)
                        workflow_observations.record_tool_call(
                            turn_id,
                            function.__name__,
                            bound.arguments,
                        )
                    except Exception:
                        # Learning is strictly ancillary. Its storage must
                        # never turn a successful Alfred tool into a failure.
                        pass
                return result

            return server.tool(
                annotations=ToolAnnotations(
                    readOnlyHint=read_only,
                    # Only meaningful when the tool writes at all; stating it
                    # for a read-only tool would imply it could destroy
                    # something.
                    destructiveHint=None if read_only else destructive,
                    idempotentHint=None if read_only else idempotent,
                    openWorldHint=open_world,
                )
            )(observed)

        return decorate

    @alfred_tool(read_only=True, idempotent=True)
    def system_status() -> dict[str, int | str]:
        """Return Alfred's non-sensitive local health and schema status."""
        return database.status()

    @alfred_tool(read_only=True, idempotent=True)
    def agenda_get() -> str:
        """Return Alfred's deterministic local task agenda with freshness."""
        policy.require_read(client_id, "agenda_get")
        return BriefingService(database).morning_brief().render()

    @alfred_tool(read_only=True, idempotent=True)
    def memory_search(query: str) -> dict:
        """Search local memory anchors and their one-hop active graph context."""
        scope = policy.require_read(client_id, "memory_search")
        return MemoryGraph(database).search(query, allowed_sensitivities=scope.allowed_sensitivities).model_dump(mode="json")

    @alfred_tool(read_only=True, idempotent=True)
    def profile_get() -> dict:
        """Return the local owner node and current, evidence-backed profile relationships."""
        scope = policy.require_read(client_id, "profile_get")
        owner, relationships = MemoryGraph(database).profile(allowed_sensitivities=scope.allowed_sensitivities)
        return {
            "owner": owner.model_dump(mode="json") if owner else None,
            "relationships": [relationship.model_dump(mode="json") for relationship in relationships],
        }

    @alfred_tool(destructive=False)
    def remember(statement: str, kind: str = "note", sensitivity: str = "personal") -> dict:
        """Store a confirmed local memory; the calling client is recorded as actor."""
        if sensitivity not in ALLOWED_SENSITIVITIES:
            raise ValueError(f"unknown sensitivity: {sensitivity}")
        scope = policy.require_write(client_id, "remember")
        if sensitivity not in scope.allowed_sensitivities:
            raise PolicyError(f"client is not scoped to write sensitivity: {sensitivity}")
        memory = MemoryGraph(database).remember(
            statement, kind=kind, sensitivity=cast(Sensitivity, sensitivity), actor=f"mcp:{client_id}"
        )
        return memory.model_dump(mode="json")

    @alfred_tool(destructive=False)
    def memory_correct(memory_id: str, replacement_statement: str) -> dict:
        """Correct one recalled memory while preserving its superseded history and evidence."""
        policy.require_write(client_id, "memory_correct")
        return MemoryGraph(database).supersede_memory(
            memory_id,
            replacement_statement,
            actor=f"mcp:{client_id}",
        ).model_dump(mode="json")

    @alfred_tool(destructive=False)
    def memory_feedback(memory_id: str, query: str, outcome: str) -> dict:
        """Record whether a recalled memory was relevant, irrelevant, or incorrect."""
        policy.require_write(client_id, "memory_feedback")
        return MemoryFeedbackStore(database).record(
            memory_id,
            query=query,
            outcome=outcome,
            actor=f"mcp:{client_id}",
        )

    @alfred_tool(destructive=False)
    def forget(memory_id: str, reason: str = "user requested deletion") -> dict:
        """Preview deleting one memory; nothing is deleted until action_commit confirms it.

        Decision 8 classifies deleting data as strong-confirm, never
        unattended, so an MCP client -- which can call tools without a human
        watching in the moment -- cannot delete in a single call.
        """
        scope = policy.require_write(client_id, "forget")
        graph = MemoryGraph(database)
        existing = graph.get_memory(memory_id)
        if existing is None:
            raise GraphError(f"memory does not exist: {memory_id}")
        if existing.sensitivity not in scope.allowed_sensitivities:
            raise PolicyError(f"client is not scoped to forget sensitivity: {existing.sensitivity}")
        approval = MemoryActions(database, approvals).propose_forget(memory_id, actor=f"mcp:{client_id}", reason=reason)
        return approval.model_dump(mode="json")

    @alfred_tool(destructive=False)
    def calendar_event_propose(summary: str, start: str, end: str, calendar_id: str = "primary") -> dict:
        """Preview a calendar event write; nothing reaches Google until action_commit confirms it."""
        policy.require_write(client_id, "calendar_event_propose")
        parsed_start, parsed_end = datetime.fromisoformat(start), datetime.fromisoformat(end)
        actions = GoogleCalendarActions(database, approvals)
        approval = actions.propose_event(
            actor=f"mcp:{client_id}", calendar_id=calendar_id, summary=summary, start=parsed_start, end=parsed_end
        )
        return approval.model_dump(mode="json")

    @alfred_tool(destructive=False)
    def message_draft(to: str, subject: str, body: str) -> dict:
        """Preview a Gmail draft; nothing reaches Gmail until a human confirms it."""
        policy.require_write(client_id, "message_draft")
        actions = GmailActions(database, approvals)
        approval = actions.propose_draft(actor=f"mcp:{client_id}", to=to, subject=subject, body=body)
        return approval.model_dump(mode="json")

    @alfred_tool(destructive=False)
    def message_send_propose(to: str, subject: str, body: str) -> dict:
        """Preview sending Gmail; a human must separately approve action_commit."""
        policy.require_write(client_id, "message_send_propose")
        return GmailSendActions(database, approvals).propose_send(
            actor=f"mcp:{client_id}", to=to, subject=subject, body=body
        ).model_dump(mode="json")

    @alfred_tool(destructive=False)
    def github_issue_propose(repository: str, title: str, body: str | None = None) -> dict:
        """Preview a GitHub issue creation; nothing reaches GitHub until action_commit confirms it."""
        policy.require_write(client_id, "github_issue_propose")
        approval = GitHubActions(database, approvals).propose_issue(
            actor=f"mcp:{client_id}", repository=repository, title=title, body=body
        )
        return approval.model_dump(mode="json")

    @alfred_tool(destructive=True, idempotent=True, open_world=True)
    def action_commit(approval_id: str, token: str) -> dict:
        """Consume a fresh approval token and perform the action it previewed."""
        policy.require_write(client_id, "action_commit")
        return ActionExecutor(database).execute(
            approval_id, actor=f"mcp:{client_id}", token=token
        )

    @alfred_tool(read_only=True, idempotent=True)
    def brief_get(now: str | None = None) -> str:
        """Render the deterministic local morning brief on demand, not just on schedule."""
        policy.require_read(client_id, "brief_get")
        parsed = datetime.fromisoformat(now) if now else None
        return BriefingService(database).morning_brief(parsed).render()

    @alfred_tool(read_only=True, idempotent=True)
    def threads_awaiting_reply() -> str:
        """List unread Gmail threads that look like they need a reply.

        Groups active unread mail by thread_id and drops messages that carry a
        List-Unsubscribe header (newsletters Gmail often labels PERSONAL).
        Run ``alfred gmail-thread-backfill`` once if older rows are missing
        thread_id / list_unsubscribe.
        """
        policy.require_read(client_id, "threads_awaiting_reply")
        return ThreadService(database).awaiting_reply().render()

    @alfred_tool(read_only=True, idempotent=True)
    def availability_get(
        days: int = 7,
        timezone: str = "UTC",
        min_minutes: int = 30,
    ) -> str:
        """Find free gaps in the synced Google Calendar over the next few days.

        Timed events block the day; all-day events are listed as ambiguous
        context rather than busy hours. Overlapping meetings merge before gaps
        are computed. ``timezone`` is an IANA name; default working hours are
        09:00–17:00 local.
        """
        policy.require_read(client_id, "availability_get")
        return AvailabilityService(database).get(
            days=days, timezone_name=timezone, min_minutes=min_minutes
        ).render()

    @alfred_tool(read_only=True, idempotent=True)
    def pull_requests_get(stale_after_days: int = 14) -> str:
        """List open GitHub pull requests you authored or were asked to review.

        Fetches a live snapshot via GitHub search (not notifications sync),
        marks PRs stale when ``updated_at`` is older than ``stale_after_days``.
        """
        policy.require_read(client_id, "pull_requests_get")
        client = GitHubClient(SystemKeyringSecretStore().get_required("github-token"))
        try:
            return PullRequestService(client, stale_after_days=stale_after_days).get().render()
        finally:
            client.close()

    @alfred_tool(read_only=True, idempotent=True)
    def connector_status() -> list[dict]:
        """Report each connector's health; never its credentials or synced content."""
        policy.require_read(client_id, "connector_status")
        return [health.model_dump(mode="json") for health in connector_health(database)]

    @alfred_tool(read_only=True, idempotent=True)
    def connector_records_get(connector: str, record_type: str | None = None, limit: int = 20) -> list[dict]:
        """Return one connector's currently-active synced records, most recently observed first.

        brief_get/agenda_get already fold calendar events, GitHub
        notifications, and Canvas missing assignments into one ranked digest,
        but nothing else exposes a connector's raw synced content directly --
        for example gmail-sync's unread-message records (subject/from/snippet)
        never reach an MCP caller otherwise. This reads the same
        connector_records table every sync already writes to
        (ConnectorRecordStore), so it needs no new storage or sync logic.
        """
        scope = policy.require_read(client_id, "connector_records_get")
        connector_sensitivity = "sensitive" if connector == "google_health" else "personal"
        if connector_sensitivity not in scope.allowed_sensitivities:
            raise PolicyError(
                f"client is not scoped to read {connector_sensitivity} connector records: {connector}"
            )
        database.migrate()
        with database.connect() as connection:
            rows = connection.execute(
                """
                SELECT record_type, record_id, payload_json, observed_at FROM connector_records
                WHERE connector = ? AND active = 1 AND (? IS NULL OR record_type = ?)
                ORDER BY observed_at DESC
                LIMIT ?
                """,
                (connector, record_type, record_type, limit),
            ).fetchall()
        records = [
            {
                "record_type": row["record_type"],
                "record_id": row["record_id"],
                "payload": json.loads(row["payload_json"]),
                "observed_at": row["observed_at"],
            }
            for row in rows
        ]
        if client_id == "hermes":
            # Tool results leave Alfred through Hermes's provider connection,
            # outside the bridge prompt boundary. Apply the same PII floor
            # here so a raw connector read cannot bypass bridge redaction.
            return json.loads(Redactor().redact(json.dumps(records)))
        return records

    @alfred_tool(destructive=False)
    def task_upsert(title: str, task_id: str | None = None, due_at: str | None = None) -> dict:
        """Create a task, or update an existing one's title/due date when task_id is given.

        Decision 8 classifies this as automatic and reversible, unlike
        deletion, so it needs no approval step.
        """
        policy.require_write(client_id, "task_upsert")
        # due_at omitted means "leave unchanged" on update ("no due date" on
        # create); the tool has no separate way to explicitly clear one.
        parsed_due = datetime.fromisoformat(due_at) if due_at else UNSET
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                if task_id is None:
                    event = EventStore.append(
                        connection,
                        source="mcp",
                        external_id=f"task:{client_id}:{uuid4()}",
                        occurred_at=datetime.now(UTC),
                        content=title,
                        metadata={"client": client_id},
                    )
                    task = TaskStore.upsert(connection, title=title, due_at=parsed_due, source_event_id=event.id)
                else:
                    task = TaskStore.upsert(connection, task_id=task_id, title=title, due_at=parsed_due)
        return task.model_dump(mode="json")

    @alfred_tool(destructive=False, idempotent=True)
    def task_complete(task_id: str) -> dict:
        """Mark an open task completed; completing an already-completed task is a no-op."""
        policy.require_write(client_id, "task_complete")
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                task = TaskStore.complete(connection, task_id)
        return task.model_dump(mode="json")

    @alfred_tool(destructive=False)
    def reminder_set(
        text: str,
        run_at: str,
        chat_id: int,
        task_id: str | None = None,
        daily: bool = False,
        timezone: str | None = None,
    ) -> dict:
        """Schedule a Telegram reminder; chat_id must already be locally paired to receive it.

        Alfred's only delivery channel today is Telegram, so the caller must
        say which paired chat this goes to -- there is no channel-agnostic
        queue to defer that choice to.

        ``daily`` repeats at the same local wall-clock time (wake-up, bedtime,
        study lock-in). When ``daily`` is true, ``timezone`` must be an IANA
        name such as America/New_York so the hour survives a daylight-saving
        change.
        """
        policy.require_write(client_id, "reminder_set")
        parsed_run_at = datetime.fromisoformat(run_at)
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                if task_id is None:
                    event = EventStore.append(
                        connection,
                        source="mcp",
                        external_id=f"reminder:{client_id}:{uuid4()}",
                        occurred_at=datetime.now(UTC),
                        content=text,
                        metadata={"client": client_id},
                    )
                    task = TaskStore.upsert(connection, title=text, due_at=parsed_run_at, source_event_id=event.id)
                    resolved_task_id = task.id
                else:
                    resolved_task_id = task_id
                job = ReminderStore.create(
                    connection,
                    run_at=parsed_run_at,
                    task_id=resolved_task_id,
                    chat_id=chat_id,
                    text=text,
                    daily=daily,
                    timezone_name=timezone,
                    idempotency_key=(
                        f"mcp-reminder:{client_id}:{resolved_task_id}:{parsed_run_at.isoformat()}"
                        + (f":daily:{timezone or ''}" if daily else "")
                    ),
                )
        return job.model_dump(mode="json")

    @alfred_tool(destructive=False)
    def nag_until_done(
        text: str,
        chat_id: int,
        interval_hours: float = 24.0,
        max_attempts: int = 5,
        task_id: str | None = None,
        first_run_at: str | None = None,
    ) -> dict:
        """Repeat a reminder until the linked task is completed or attempts run out.

        Each firing re-reads task state, so completing the task anywhere silences
        future nags. The final attempt is labeled explicitly as the last reminder.
        """
        policy.require_write(client_id, "nag_until_done")
        if first_run_at is None:
            run_at = datetime.now(UTC) + timedelta(hours=interval_hours)
        else:
            run_at = datetime.fromisoformat(first_run_at)
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                if task_id is None:
                    event = EventStore.append(
                        connection,
                        source="mcp",
                        external_id=f"nag:{client_id}:{uuid4()}",
                        occurred_at=datetime.now(UTC),
                        content=text,
                        metadata={"client": client_id},
                    )
                    task = TaskStore.upsert(connection, title=text, source_event_id=event.id)
                    resolved_task_id = task.id
                else:
                    resolved_task_id = task_id
                job = NagStore.create(
                    connection,
                    run_at=run_at,
                    task_id=resolved_task_id,
                    chat_id=chat_id,
                    text=text,
                    interval_hours=interval_hours,
                    max_attempts=max_attempts,
                    idempotency_key=(
                        f"mcp-nag:{client_id}:{resolved_task_id}:{interval_hours}:{max_attempts}"
                    ),
                )
        return job.model_dump(mode="json")

    @alfred_tool(destructive=False)
    def important_date_set(
        label: str,
        month: int,
        day: int,
        chat_id: int,
        timezone: str,
        kind: str = "birthday",
        year: int | None = None,
    ) -> dict:
        """Remember a birthday or other annual date and remind on that day each year.

        Stored as an ordinary task (next occurrence as due_at) plus an annual
        reminder job — not a separate calendar. The morning brief and weekly
        window surface dates in the next seven days under Birthdays & dates.
        ``timezone`` is an IANA name so the local morning of the date survives
        daylight saving. ``year`` is optional and only used to say "turns N".
        """
        policy.require_write(client_id, "important_date_set")
        if kind not in {"birthday", "anniversary", "other"}:
            raise ValueError("kind must be birthday, anniversary, or other")
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                recorded = ImportantDateStore.record(
                    connection,
                    label=label,
                    month=month,
                    day=day,
                    kind=kind,  # type: ignore[arg-type]
                    year=year,
                    chat_id=chat_id,
                    timezone_name=timezone,
                )
        return recorded.model_dump(mode="json")

    @alfred_tool(read_only=True, idempotent=True)
    def important_dates_get(within_days: int = 7) -> list[dict]:
        """List upcoming birthdays and important dates inside the weekly window.

        Defaults to seven days so a "what's coming up this week" question and
        the morning brief share the same horizon. Pass a larger window to look
        further ahead; pass 0 for only dates still later today.
        """
        policy.require_read(client_id, "important_dates_get")
        return [
            item.model_dump(mode="json")
            for item in ImportantDateStore.upcoming(database, within_days=within_days)
        ]

    @alfred_tool(destructive=False)
    def mood_record(rating: int, note: str | None = None) -> dict:
        """Record a 1–5 mood check-in with an optional short note.

        Stored separately from habits: mood tracks how things felt, not whether
        a behavior happened. Use ``journal_get`` to review recent entries and
        whether a trend can be named.
        """
        policy.require_write(client_id, "mood_record")
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                recorded = JournalStore.mood_record(connection, rating=rating, note=note)
        return recorded.model_dump(mode="json")

    @alfred_tool(destructive=False)
    def gratitude_record(text: str) -> dict:
        """Append a free-text gratitude journal entry."""
        policy.require_write(client_id, "gratitude_record")
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                recorded = JournalStore.gratitude_record(connection, text=text)
        return recorded.model_dump(mode="json")

    @alfred_tool(read_only=True, idempotent=True)
    def journal_get(days: int = 30) -> dict:
        """Return recent mood check-ins, gratitude entries, and mood trend.

        Trend direction is only named when there are at least five days with mood
        check-ins and the older/newer daily averages differ by at least 0.5 on
        the 1–5 scale; otherwise ``mood_trend.reason`` explains the refusal so
        a null direction does not read as "no change".
        """
        policy.require_read(client_id, "journal_get")
        snapshot = JournalStore.get(database, days=days)
        return snapshot.model_dump(mode="json")

    @alfred_tool(destructive=False)
    def task_schedule(
        prompt: str, run_at: str, chat_id: int, daily: bool = False, timezone: str | None = None
    ) -> dict:
        """Run an instruction later and send the answer to a paired chat.

        Use this, not a reminder, when the user wants something *done* at a
        time rather than something *said*: "check the order at 3 and text me"
        has no message to deliver yet, because the answer does not exist until
        the work runs. A reminder would just hand the task back to them.

        When it comes due the instruction is queued as an ordinary agent turn,
        so the reply arrives looking exactly like any other answer. Never
        schedule this kind of work in your own runtime's cron: Alfred owns
        schedules and delivery here, and a job elsewhere silently never fires.

        ``run_at`` is ISO-8601 with an offset. ``daily`` repeats it, and then
        ``timezone`` must be an IANA name (America/New_York) so the task keeps
        its local hour across a daylight-saving change.
        """
        policy.require_write(client_id, "task_schedule")
        parsed_run_at = datetime.fromisoformat(run_at)
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                task = ScheduledTaskStore.schedule(
                    connection,
                    prompt=prompt,
                    run_at=parsed_run_at,
                    chat_id=chat_id,
                    daily=daily,
                    timezone_name=timezone,
                    idempotency_key=(
                        f"mcp-task:{client_id}:{chat_id}:{parsed_run_at.isoformat()}:{prompt.strip()[:80]}"
                    ),
                )
        return task.model_dump(mode="json")

    if tool_filter is not None:
        unknown = tool_filter - MCP_TOOL_NAMES
        if unknown:
            raise ValueError(f"unknown MCP tool filter entries: {', '.join(sorted(unknown))}")
        for tool_name in MCP_TOOL_NAMES - tool_filter:
            server.remove_tool(tool_name)
    return server


def _tool_filter_from_environment() -> frozenset[str] | None:
    value = os.environ.get(HERMES_MCP_TOOL_FILTER_ENV)
    if value is None:
        return None
    return frozenset(name.strip() for name in value.split(",") if name.strip())


def parse_stdio_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse alfred-mcp's own tiny CLI surface.

    Separate from ``main()`` so a caller (or a test) can get a parsed
    namespace without also starting a blocking stdio server.
    """
    parser = argparse.ArgumentParser(prog="alfred-mcp", description="Alfred's stdio MCP server")
    parser.add_argument(
        "--client-id",
        default="local-mcp",
        help="local client identity; must already have its own 'alfred client-grant' scope (default: local-mcp)",
    )
    parser.add_argument("--db", help="SQLite database path; defaults to ALFRED_DB_PATH or .alfred/alfred.db")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """Run Alfred's local-only stdio MCP server.

    Running this with no arguments behaves exactly as before --
    ``--client-id`` exists so a second stdio client (for example, OpenAI's
    Secure MCP Tunnel `tunnel-client`, launched via its own `--mcp-command`)
    can get its own separately scoped identity instead of sharing
    Claude/Cursor's default ``local-mcp`` grant.
    """
    args = parse_stdio_args(argv)
    tool_filter = _tool_filter_from_environment()
    if tool_filter is None:
        create_server(args.db, client_id=args.client_id).run(transport="stdio")
    else:
        create_server(args.db, client_id=args.client_id, tool_filter=tool_filter).run(transport="stdio")


def run_streamable_http(
    database_path: Path | str | None = None,
    *,
    client_id: str,
    port: int,
    bearer_token: str,
) -> None:
    """Serve Alfred's MCP surface over Streamable HTTP, loopback-only.

    The host is deliberately not a parameter: this always binds
    ``127.0.0.1``, matching section 7's "Local server binds 127.0.0.1 only"
    as a hard invariant rather than a default that could be overridden away
    from it. FastMCP auto-enables DNS-rebinding protection (Host/Origin
    header validation) whenever the host is a loopback address, so no extra
    ``transport_security`` wiring is needed as long as this stays that way.
    Every request additionally needs the exact configured bearer token --
    see the module docstring for why that, not OAuth, is enough here.

    ``client_id`` must already have a scope from ``PolicyStore.grant()``
    (the CLI's ``client-grant``) before any tool call succeeds; this
    function itself performs no default grant.
    """
    import uvicorn

    server = create_server(database_path, client_id=client_id)
    protected_app = _BearerAuthMiddleware(server.streamable_http_app(), expected_token=bearer_token)
    uvicorn.Server(uvicorn.Config(protected_app, host="127.0.0.1", port=port, log_level="warning")).run()


if __name__ == "__main__":
    main()
