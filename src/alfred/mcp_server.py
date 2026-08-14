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
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence, cast
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from .briefing import BriefingService
from .config import Settings
from .connector_health import connector_health
from .db import Database
from .events import EventStore
from .gmail import GmailActions, GmailClient, GmailSendActions
from .github import GitHubActions, GitHubClient
from .google_calendar import GoogleCalendarActions, GoogleCalendarClient
from .google_oauth import current_access_token
from .hermes_tools import HERMES_MCP_TOOL_FILTER_ENV
from .http_auth import BearerAuthMiddleware as _BearerAuthMiddleware
from .http_auth import bearer_token as _bearer_token
from .http_auth import generate_token as generate_http_token
from .memory_graph import GraphError, MemoryActions, MemoryGraph, Sensitivity
from .memory_learning import MemoryFeedbackStore
from .models import Redactor
from .policy import ApprovalService, PolicyError, PolicyStore
from .reminders import ReminderStore
from .secret_store import SystemKeyringSecretStore
from .tasks import UNSET, TaskStore

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
    server = FastMCP("Alfred")

    @server.tool()
    def system_status() -> dict[str, int | str]:
        """Return Alfred's non-sensitive local health and schema status."""
        return database.status()

    @server.tool()
    def agenda_get() -> str:
        """Return Alfred's deterministic local task agenda with freshness."""
        policy.require_read(client_id, "agenda_get")
        return BriefingService(database).morning_brief().render()

    @server.tool()
    def memory_search(query: str) -> dict:
        """Search local memory anchors and their one-hop active graph context."""
        scope = policy.require_read(client_id, "memory_search")
        return MemoryGraph(database).search(query, allowed_sensitivities=scope.allowed_sensitivities).model_dump(mode="json")

    @server.tool()
    def profile_get() -> dict:
        """Return the local owner node and current, evidence-backed profile relationships."""
        scope = policy.require_read(client_id, "profile_get")
        owner, relationships = MemoryGraph(database).profile(allowed_sensitivities=scope.allowed_sensitivities)
        return {
            "owner": owner.model_dump(mode="json") if owner else None,
            "relationships": [relationship.model_dump(mode="json") for relationship in relationships],
        }

    @server.tool()
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

    @server.tool()
    def memory_correct(memory_id: str, replacement_statement: str) -> dict:
        """Correct one recalled memory while preserving its superseded history and evidence."""
        policy.require_write(client_id, "memory_correct")
        return MemoryGraph(database).supersede_memory(
            memory_id,
            replacement_statement,
            actor=f"mcp:{client_id}",
        ).model_dump(mode="json")

    @server.tool()
    def memory_feedback(memory_id: str, query: str, outcome: str) -> dict:
        """Record whether a recalled memory was relevant, irrelevant, or incorrect."""
        policy.require_write(client_id, "memory_feedback")
        return MemoryFeedbackStore(database).record(
            memory_id,
            query=query,
            outcome=outcome,
            actor=f"mcp:{client_id}",
        )

    @server.tool()
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

    @server.tool()
    def calendar_event_propose(summary: str, start: str, end: str, calendar_id: str = "primary") -> dict:
        """Preview a calendar event write; nothing reaches Google until action_commit confirms it."""
        policy.require_write(client_id, "calendar_event_propose")
        parsed_start, parsed_end = datetime.fromisoformat(start), datetime.fromisoformat(end)
        actions = GoogleCalendarActions(database, approvals)
        approval = actions.propose_event(
            actor=f"mcp:{client_id}", calendar_id=calendar_id, summary=summary, start=parsed_start, end=parsed_end
        )
        return approval.model_dump(mode="json")

    @server.tool()
    def message_draft(to: str, subject: str, body: str) -> dict:
        """Preview a Gmail draft; nothing reaches Gmail until action_commit confirms it.

        This creates a draft only -- Alfred's code never calls a send
        endpoint. Sending is connector order's next phase and stays unbuilt.
        """
        policy.require_write(client_id, "message_draft")
        actions = GmailActions(database, approvals)
        approval = actions.propose_draft(actor=f"mcp:{client_id}", to=to, subject=subject, body=body)
        return approval.model_dump(mode="json")

    @server.tool()
    def message_send_propose(to: str, subject: str, body: str) -> dict:
        """Preview sending Gmail; a human must separately approve action_commit."""
        policy.require_write(client_id, "message_send_propose")
        return GmailSendActions(database, approvals).propose_send(
            actor=f"mcp:{client_id}", to=to, subject=subject, body=body
        ).model_dump(mode="json")

    @server.tool()
    def github_issue_propose(repository: str, title: str, body: str | None = None) -> dict:
        """Preview a GitHub issue creation; nothing reaches GitHub until action_commit confirms it."""
        policy.require_write(client_id, "github_issue_propose")
        approval = GitHubActions(database, approvals).propose_issue(
            actor=f"mcp:{client_id}", repository=repository, title=title, body=body
        )
        return approval.model_dump(mode="json")

    @server.tool()
    def action_commit(approval_id: str, token: str) -> dict:
        """Consume a fresh approval token and perform the action it previewed."""
        policy.require_write(client_id, "action_commit")
        actor = f"mcp:{client_id}"
        approval = approvals.get(approval_id)
        if approval is None:
            raise PolicyError("approval does not exist")
        if approval.action_type == "memory_forget":
            receipt = MemoryActions(database, approvals).execute_forget(approval_id, actor=actor, token=token)
            return receipt.model_dump(mode="json")
        if approval.action_type == "calendar_event_create":
            client = GoogleCalendarClient(current_access_token(SystemKeyringSecretStore()))
            try:
                receipt = GoogleCalendarActions(database, approvals, client).execute(approval_id, actor=actor, token=token)
            finally:
                client.close()
            return receipt.model_dump(mode="json")
        if approval.action_type == "gmail_draft_create":
            client = GmailClient(current_access_token(SystemKeyringSecretStore()))
            try:
                receipt = GmailActions(database, approvals, client).execute(approval_id, actor=actor, token=token)
            finally:
                client.close()
            return receipt.model_dump(mode="json")
        if approval.action_type == "github_issue_create":
            client = GitHubClient(SystemKeyringSecretStore().get_required("github-issue-token"))
            try:
                receipt = GitHubActions(database, approvals, client).execute(approval_id, actor=actor, token=token)
            finally:
                client.close()
            return receipt.model_dump(mode="json")
        if approval.action_type == "gmail_message_send":
            client = GmailClient(current_access_token(SystemKeyringSecretStore()))
            try:
                receipt = GmailSendActions(database, approvals, client).execute(approval_id, actor=actor, token=token)
            finally:
                client.close()
            return receipt.model_dump(mode="json")
        raise PolicyError(f"action_commit does not yet support this action type: {approval.action_type}")

    @server.tool()
    def brief_get(now: str | None = None) -> str:
        """Render the deterministic local morning brief on demand, not just on schedule."""
        policy.require_read(client_id, "brief_get")
        parsed = datetime.fromisoformat(now) if now else None
        return BriefingService(database).morning_brief(parsed).render()

    @server.tool()
    def connector_status() -> list[dict]:
        """Report each connector's health; never its credentials or synced content."""
        policy.require_read(client_id, "connector_status")
        return [health.model_dump(mode="json") for health in connector_health(database)]

    @server.tool()
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

    @server.tool()
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

    @server.tool()
    def task_complete(task_id: str) -> dict:
        """Mark an open task completed; completing an already-completed task is a no-op."""
        policy.require_write(client_id, "task_complete")
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                task = TaskStore.complete(connection, task_id)
        return task.model_dump(mode="json")

    @server.tool()
    def reminder_set(text: str, run_at: str, chat_id: int, task_id: str | None = None) -> dict:
        """Schedule a Telegram reminder; chat_id must already be locally paired to receive it.

        Alfred's only delivery channel today is Telegram, so the caller must
        say which paired chat this goes to -- there is no channel-agnostic
        queue to defer that choice to.
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
                    idempotency_key=f"mcp-reminder:{client_id}:{resolved_task_id}:{parsed_run_at.isoformat()}",
                )
        return job.model_dump(mode="json")

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
