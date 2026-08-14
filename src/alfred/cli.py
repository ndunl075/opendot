"""Small CLI that exercises the same core used by future transports."""

from __future__ import annotations

import argparse
import getpass
import json
from contextlib import contextmanager
from uuid import uuid4
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Iterator, Sequence

from .audit import AuditEvent, AuditLog
from .academic_memory import AcademicMemoryService
from .historical_memory import HistoricalMemoryService
from .embeddings import EmbeddingBackfill, OllamaEmbeddingProvider
from .backup import EncryptedBackupService
from .config import Settings
from .connector_health import connector_health
from .db import Database
from .briefing import BriefingService
from .events import EventStore
from .jobs import JobRunner
from .memory_graph import MemoryActions, MemoryGraph
from .memory_learning import MemoryLearningService
from .reminders import ReminderStore
from .tasks import UNSET, TaskStore
from .vault import VaultImporter, VaultProjector
from .policy import ApprovalService, PolicyStore
from .secret_store import SecretStoreError, SystemKeyringSecretStore
from .google_calendar import (
    CalendarCatalogSync,
    GoogleCalendarActions,
    GoogleCalendarClient,
    GoogleCalendarHistorySync,
    GoogleCalendarSync,
    default_sync_window,
)
from .google_oauth import DEFAULT_SCOPES, authorize_interactively, current_access_token
from .canvas import CanvasClient, CanvasSync
from .canvas_ical import (
    CanvasICalClient,
    CanvasICalSync,
    CanvasICalSyncResult,
    setup_canvas_ical_feed,
)
from .google_health import GoogleHealthClient, GoogleHealthSync
from .github import GitHubActions, GitHubClient, GitHubNotificationsSync
from .gmail import DEFAULT_UNREAD_LIMIT, GmailActions, GmailClient, GmailSendActions, GmailSync
from .gmail_inbound import GmailInboundGateway
from .hermes_bridge import HermesBridge, SubprocessAgentRunner
from .brief_schedule import create_daily
from .telegram_bot import TelegramBotClient
from .telegram_runtime import TelegramLongPoller, TelegramOutboxWorker
from .runner import AlfredRunner, ConnectorSync
from .telegram import TelegramGateway, TelegramPair, TelegramUpdate
from .slack import SlackGateway, SlackPair
from .slack_socket import SlackBotClient, SlackSocketReceiver
from .mcp_server import generate_http_token, run_streamable_http
from .admin_ui import run_admin_ui
from .winservice import configure as configure_windows_service
from .vault_sync import check_couchdb


def database_from_args(args: argparse.Namespace) -> Database:
    settings = Settings.from_environment(Path(args.db) if args.db else None)
    return Database(settings.database_path)


@contextmanager
def running_alfred_runner(database: Database, args: argparse.Namespace) -> Iterator[AlfredRunner]:
    """Build and tear down the exact ``AlfredRunner`` behind ``alfred run``.

    ``args`` is a parsed ``run`` namespace (from ``build_parser()``).
    Shared with ``alfred.winservice`` so a Windows service drives the
    identical construction and cleanup path as the CLI -- only how the loop
    is told to stop differs between the two callers (``KeyboardInterrupt``
    here; a ``stop_check`` there).
    """
    pairs = frozenset(_parse_telegram_pair(value) for value in args.pair)
    chat_ids = frozenset(args.chat_id)
    slack_pairs = frozenset(_parse_slack_pair(value) for value in args.slack_pair)
    slack_channel_ids = frozenset(args.slack_channel_id)
    embedding_provider = (
        OllamaEmbeddingProvider(model_name=args.embedding_model) if args.embedding_model else None
    )
    telegram_transport = (
        TelegramBotClient(SystemKeyringSecretStore().get_required(args.telegram_secret_name))
        if pairs or chat_ids
        else None
    )
    slack_bot = (
        SlackBotClient(SystemKeyringSecretStore().get_required(args.slack_bot_secret_name))
        if slack_pairs or slack_channel_ids
        else None
    )
    slack_receiver = (
        SlackSocketReceiver(
            app_token=SystemKeyringSecretStore().get_required(args.slack_app_secret_name),
            bot_client=slack_bot,
            gateway=SlackGateway(database, set(slack_pairs)),
        )
        if slack_pairs and slack_bot is not None
        else None
    )
    connectors: list[ConnectorSync] = [
        ConnectorSync(
            name="google_calendar",
            interval_seconds=args.connector_interval,
            run=lambda: _calendar_sync_once(database, args.calendar_id),
        ),
        ConnectorSync(
            name="github",
            interval_seconds=args.connector_interval,
            run=lambda: _github_sync_once(database, args.github_secret_name),
        ),
        ConnectorSync(
            name="gmail",
            interval_seconds=args.connector_interval,
            run=lambda: _gmail_sync_once(database, args.gmail_unread_limit),
        ),
    ]
    if args.gmail_inbound_sender:
        gmail_inbound_senders = set(args.gmail_inbound_sender)
        gmail_inbound_destination = args.gmail_inbound_destination
        connectors.append(
            ConnectorSync(
                name="gmail_inbound",
                interval_seconds=args.connector_interval,
                run=lambda: _gmail_inbound_poll_once(
                    database, gmail_inbound_senders, gmail_inbound_destination, args.gmail_unread_limit
                ),
            )
        )
    if args.canvas_base_url:
        connectors.append(
            ConnectorSync(
                name="canvas",
                interval_seconds=args.connector_interval,
                run=lambda: _canvas_sync_once(
                    database, args.canvas_base_url, args.canvas_secret_name, include_history=False
                ),
            )
        )
        connectors.append(
            ConnectorSync(
                name="canvas_history",
                interval_seconds=args.canvas_history_interval,
                run=lambda: _canvas_sync_once(
                    database, args.canvas_base_url, args.canvas_secret_name, include_history=True
                ),
            )
        )
    if args.canvas_ical:
        connectors.append(
            ConnectorSync(
                name="canvas_ical",
                interval_seconds=args.canvas_ical_interval,
                run=lambda: _canvas_ical_sync_once(database, args.canvas_ical_secret_name),
            )
        )
    if args.google_health:
        connectors.append(
            ConnectorSync(
                name="google_health",
                interval_seconds=args.connector_interval,
                run=lambda: _health_sync_once(database, args.google_health_lookback_days),
            )
        )
    if args.vault:
        connectors.append(
            ConnectorSync(
                name="obsidian_vault",
                interval_seconds=args.connector_interval,
                run=lambda: VaultImporter(database, args.vault).sync(),
            )
        )
    if args.calendar_history_days:
        connectors.append(
            ConnectorSync(
                name="google_calendar_history",
                interval_seconds=args.calendar_history_interval,
                run=lambda: _calendar_history_sync_once(
                    database,
                    args.calendar_id,
                    days=args.calendar_history_days,
                    minimum_age_seconds=args.calendar_history_interval,
                ),
            )
        )
    # Derived history is always last: connector reads land first, then this
    # cheap idempotent pass precomputes context for future agent turns. It is
    # never part of the Telegram response path.
    connectors.append(
        ConnectorSync(
            name="academic_memory",
            interval_seconds=args.connector_interval,
            run=AcademicMemoryService(database).rebuild_if_changed,
        )
    )
    connectors.append(
        ConnectorSync(
            name="historical_memory",
            interval_seconds=args.connector_interval,
            run=HistoricalMemoryService(database).rebuild_if_changed,
        )
    )
    if embedding_provider is not None:
        connectors.append(
            ConnectorSync(
                name="memory_embeddings",
                interval_seconds=args.connector_interval,
                run=lambda: EmbeddingBackfill(database, embedding_provider).run(limit=128),
            )
        )
    agent_bridge = None
    if args.hermes_profile:
        memory_graph = (
            MemoryGraph(database, embedding_provider=embedding_provider)
            if embedding_provider is not None
            else MemoryGraph(database)
        )
        agent_bridge = HermesBridge(
            database,
            SubprocessAgentRunner(
                command=args.hermes_command,
                profile=args.hermes_profile,
                timeout_seconds=args.hermes_timeout,
                database=database,
                monthly_call_limit=args.hermes_monthly_call_limit,
            ),
            memory_graph=memory_graph,
        ).run_once
    runner = AlfredRunner(
        database,
        telegram_transport=telegram_transport,
        telegram_pairs=pairs,
        telegram_chat_ids=chat_ids,
        defer_unparsed_to_agent=bool(args.hermes_profile),
        agent_bridge=agent_bridge,
        memory_learning=MemoryLearningService(database).run_once if args.hermes_profile else None,
        slack_transport=slack_bot,
        slack_pairs=slack_pairs,
        slack_channel_ids=slack_channel_ids,
        connectors=tuple(connectors),
        poll_timeout_seconds=args.poll_timeout,
        idle_sleep_seconds=args.idle_sleep,
    )
    try:
        if slack_receiver is not None:
            slack_receiver.start()
        yield runner
    finally:
        if telegram_transport is not None:
            telegram_transport.close()
        if slack_receiver is not None:
            slack_receiver.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alfred", description="Alfred Core local CLI")
    parser.add_argument("--db", help="SQLite database path; defaults to ALFRED_DB_PATH or .alfred/alfred.db")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("init", help="create or migrate the local database")
    subcommands.add_parser("status", help="show non-sensitive local status")
    subcommands.add_parser(
        "academic-memory-rebuild",
        help="rebuild local Calendar/Canvas rollups and their provenance-linked semantic memories",
    )
    subcommands.add_parser("connector-status", help="show each connector's health without exposing credentials")
    vault_sync_status = subcommands.add_parser(
        "vault-sync-status", help="check whether the self-hosted CouchDB behind optional mobile vault sync is reachable"
    )
    vault_sync_status.add_argument("--url", default="http://127.0.0.1:5984", help="the CouchDB instance's base URL")
    backup_create = subcommands.add_parser("backup-create", help="create an encrypted local Alfred database backup")
    backup_create.add_argument("--output", type=Path, required=True)
    backup_create.add_argument("--secret-name", default="backup-encryption-key")
    backup_key = subcommands.add_parser("backup-key-generate", help="generate and store a new local backup encryption key")
    backup_key.add_argument("--secret-name", default="backup-encryption-key")
    backup_restore_propose = subcommands.add_parser("backup-restore-propose", help="preview restoring an encrypted backup")
    backup_restore_propose.add_argument("--backup", type=Path, required=True)
    backup_restore_propose.add_argument("--actor", required=True)
    backup_restore_execute = subcommands.add_parser("backup-restore-execute", help="restore an approved encrypted backup")
    backup_restore_execute.add_argument("--approval-id", required=True)
    backup_restore_execute.add_argument("--actor", required=True)
    backup_restore_execute.add_argument("--token", required=True)
    backup_restore_execute.add_argument("--secret-name", default="backup-encryption-key")
    audit = subcommands.add_parser("audit", help="append a redacted audit record")
    audit.add_argument("--actor", required=True)
    audit.add_argument("--tool", required=True)
    audit.add_argument("--outcome", required=True)
    audit.add_argument("--client", default="cli")
    audit.add_argument("--arguments", default="{}", help="JSON object; do not include secrets")
    audit.add_argument("--result", default="{}", help="JSON object; do not include secrets")
    audit.add_argument("--correlation-id")
    subcommands.add_parser("audit-verify", help="verify the local audit hash chain")
    telegram = subcommands.add_parser("telegram-handle", help="handle one paired Telegram update from JSON")
    telegram_input = telegram.add_mutually_exclusive_group(required=True)
    telegram_input.add_argument("--update", help="Telegram update JSON object")
    telegram_input.add_argument("--update-file", type=Path, help="path to a Telegram update JSON object")
    telegram.add_argument("--chat-id", required=True, type=int, help="locally paired Telegram chat ID")
    telegram.add_argument("--user-id", required=True, type=int, help="locally paired Telegram user ID")
    run_due = subcommands.add_parser("run-due", help="move due jobs to the delivery outbox")
    run_due.add_argument("--now", help="ISO-8601 time for deterministic operation or tests")
    brief = subcommands.add_parser("brief", help="render the deterministic local morning brief")
    brief.add_argument("--now", help="ISO-8601 time for deterministic operation or tests")
    schedule_brief = subcommands.add_parser("schedule-brief", help="schedule one local daily morning brief")
    schedule_brief_destination = schedule_brief.add_mutually_exclusive_group(required=True)
    schedule_brief_destination.add_argument("--chat-id", type=int, help="paired Telegram chat ID (legacy shortcut)")
    schedule_brief_destination.add_argument("--destination", help="explicit delivery target, e.g. telegram:20")
    schedule_brief.add_argument("--at", required=True, help="local 24-hour HH:MM time")
    schedule_brief.add_argument("--timezone", required=True, help="IANA timezone, e.g. America/New_York")
    task_upsert = subcommands.add_parser("task-upsert", help="create a task, or update one's title/due date by --task-id")
    task_upsert.add_argument("title")
    task_upsert.add_argument("--task-id", help="update this existing task instead of creating a new one")
    task_upsert.add_argument("--due-at", help="ISO-8601 time with timezone")
    task_complete = subcommands.add_parser("task-complete", help="mark an open task completed")
    task_complete.add_argument("--task-id", required=True)
    reminder_set = subcommands.add_parser("reminder-set", help="schedule a local reminder")
    reminder_set.add_argument("text")
    reminder_set.add_argument("--run-at", required=True, help="ISO-8601 time with timezone")
    reminder_destination = reminder_set.add_mutually_exclusive_group(required=True)
    reminder_destination.add_argument("--chat-id", type=int, help="paired Telegram chat ID (legacy shortcut)")
    reminder_destination.add_argument("--destination", help="explicit delivery target, e.g. telegram:20")
    reminder_set.add_argument("--task-id", help="link to an existing task instead of creating a new one")
    self_node = subcommands.add_parser("memory-self", help="create Alfred's one owner identity")
    self_node.add_argument("--label", required=True)
    entity = subcommands.add_parser("memory-entity", help="create a confirmed graph entity")
    entity.add_argument("--type", required=True)
    entity.add_argument("--label", required=True)
    entity.add_argument("--domain", action="append", default=[])
    relation = subcommands.add_parser("memory-relation", help="create a typed temporal graph relationship")
    relation.add_argument("--source-id", required=True)
    relation.add_argument("--predicate", required=True)
    relation.add_argument("--target-id", required=True)
    relation.add_argument("--kind", choices=("state", "event"))
    relation.add_argument("--cardinality", choices=("single", "multi"))
    relation.add_argument("--valid-from", help="ISO-8601 time; defaults to now")
    relation.add_argument("--domain", action="append", default=[])
    alias = subcommands.add_parser("memory-alias", help="add a searchable alternate name for an entity")
    alias.add_argument("--entity-id", required=True)
    alias.add_argument("alias")
    remember = subcommands.add_parser("remember", help="store a confirmed local memory")
    remember.add_argument("statement")
    remember.add_argument("--kind", default="note")
    search = subcommands.add_parser("memory-search", help="search local memories and graph anchors")
    search.add_argument("query")
    embed_backfill = subcommands.add_parser(
        "memory-embed-backfill", help="build missing memory embeddings with a local Ollama model"
    )
    embed_backfill.add_argument("--model", default="nomic-embed-text")
    embed_backfill.add_argument("--base-url", default="http://127.0.0.1:11434")
    embed_backfill.add_argument("--limit", type=int)
    correct = subcommands.add_parser("memory-correct", help="supersede a memory with a corrected statement")
    correct.add_argument("--memory-id", required=True)
    correct.add_argument("statement")
    forget_propose = subcommands.add_parser(
        "memory-forget-propose", help="preview deleting one local memory; nothing is deleted until executed"
    )
    forget_propose.add_argument("--memory-id", required=True)
    forget_propose.add_argument("--reason", default="user requested deletion")
    forget_propose.add_argument("--actor", required=True)
    forget_execute = subcommands.add_parser(
        "memory-forget-execute", help="consume a fresh approval token and delete the previewed memory"
    )
    forget_execute.add_argument("--approval-id", required=True)
    forget_execute.add_argument("--actor", required=True)
    forget_execute.add_argument("--token", required=True)
    forget_source_propose = subcommands.add_parser(
        "memory-forget-source-propose", help="preview deleting all active memories from one source event"
    )
    forget_source_propose.add_argument("--source-event-id", required=True)
    forget_source_propose.add_argument("--reason", default="user requested deletion")
    forget_source_propose.add_argument("--actor", required=True)
    forget_source_execute = subcommands.add_parser(
        "memory-forget-source-execute", help="consume a fresh approval and delete the previewed source-event memories"
    )
    forget_source_execute.add_argument("--approval-id", required=True)
    forget_source_execute.add_argument("--actor", required=True)
    forget_source_execute.add_argument("--token", required=True)
    export_entity = subcommands.add_parser("vault-export-entity", help="project one entity into local Markdown")
    export_entity.add_argument("--vault", type=Path, default=Path("alfred-vault"))
    export_entity.add_argument("--entity-id", required=True)
    export_memory = subcommands.add_parser("vault-export-memory", help="project one confirmed memory into local Markdown")
    export_memory.add_argument("--vault", type=Path, default=Path("alfred-vault"))
    export_memory.add_argument("--memory-id", required=True)
    export_source = subcommands.add_parser("vault-export-source-event", help="project confirmed vault-safe memories from one source event")
    export_source.add_argument("--vault", type=Path, default=Path("alfred-vault"))
    export_source.add_argument("--source-event-id", required=True)
    import_vault = subcommands.add_parser(
        "vault-import", help="import changed user-authored vault notes as confirmed memories"
    )
    import_vault.add_argument("--vault", type=Path, default=Path("alfred-vault"))
    grant = subcommands.add_parser("client-grant", help="grant a local client explicit scoped access")
    grant.add_argument("--client-id", required=True)
    grant.add_argument("--sensitivity", action="append", default=[])
    grant.add_argument("--tool", action="append", default=[])
    grant.add_argument("--allow-write", action="store_true")
    http_token = subcommands.add_parser(
        "mcp-http-token-generate", help="generate and store the Streamable HTTP MCP bearer token"
    )
    http_token.add_argument("--secret-name", default="mcp-http-bearer-token")
    http_run = subcommands.add_parser(
        "mcp-http-run", help="run Alfred's MCP surface over Streamable HTTP, bound to 127.0.0.1 only"
    )
    http_run.add_argument("--client-id", required=True, help="must already have a client-grant scope")
    http_run.add_argument("--port", type=int, default=8000)
    http_run.add_argument("--secret-name", default="mcp-http-bearer-token")
    admin_token = subcommands.add_parser(
        "admin-ui-token-generate", help="generate and store the admin dashboard's bearer token"
    )
    admin_token.add_argument("--secret-name", default="admin-ui-bearer-token")
    admin_run = subcommands.add_parser(
        "admin-ui-run", help="run Alfred's read-only admin dashboard, bound to 127.0.0.1 by default"
    )
    admin_run.add_argument("--port", type=int, default=8200)
    admin_run.add_argument("--secret-name", default="admin-ui-bearer-token")
    admin_run.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address; 127.0.0.1 is unreachable from another device even over a VPN -- "
        "pass this host's own VPN/Tailscale IP to view the dashboard from a phone, never 0.0.0.0 "
        "unless your own firewall already restricts who can reach this port",
    )
    propose = subcommands.add_parser("approval-propose", help="create a local preview approval")
    propose.add_argument("--actor", required=True)
    propose.add_argument("--action-type", required=True)
    propose.add_argument("--preview", required=True, help="JSON object without secrets")
    approve = subcommands.add_parser("approval-approve", help="approve a local preview and issue a one-time token")
    approve.add_argument("--approval-id", required=True)
    approve.add_argument("--actor", required=True)
    consume = subcommands.add_parser("approval-consume", help="consume a fresh local approval token once")
    consume.add_argument("--approval-id", required=True)
    consume.add_argument("--actor", required=True)
    consume.add_argument("--token", required=True)
    poll = subcommands.add_parser("telegram-poll", help="long-poll Telegram locally once")
    poll.add_argument("--pair", action="append", required=True, help="locally paired CHAT_ID:USER_ID")
    poll.add_argument("--secret-name", default="telegram-bot-token")
    poll.add_argument("--timeout", type=int, default=25)
    deliver = subcommands.add_parser("telegram-deliver", help="deliver local pending Telegram outbox messages")
    deliver.add_argument("--chat-id", action="append", required=True, type=int, help="locally allowed destination chat ID")
    deliver.add_argument("--secret-name", default="telegram-bot-token")
    deliver.add_argument("--limit", type=int, default=20)
    google_auth = subcommands.add_parser(
        "google-auth",
        help="one-time interactive OAuth grant for Calendar + Gmail; stores a refresh token locally",
    )
    google_auth.add_argument("--port", type=int, default=8765, help="local loopback redirect port")
    google_auth.add_argument("--scope", action="append", default=[], help="override the default OAuth scopes")
    google_auth.add_argument("--no-browser", action="store_true", help="print the URL instead of opening a browser")
    google_auth.add_argument("--timeout", type=int, default=300, help="seconds to wait for the browser redirect")
    calendar_sync = subcommands.add_parser("calendar-sync", help="read-sync Google Calendar into local source events")
    calendar_sync.add_argument("--calendar-id", default="primary")
    calendar_sync.add_argument("--days", type=int, default=14, help="initial sync window length (1-90 days)")
    calendar_history_sync = subcommands.add_parser(
        "calendar-history-sync",
        help="read-sync past Google Calendar events without changing the live incremental cursor",
    )
    calendar_history_sync.add_argument("--calendar-id", default="primary")
    calendar_history_sync.add_argument("--days", type=int, default=1095, help="past days to backfill (1-3650)")
    calendar_history_sync.add_argument(
        "--all-selected",
        action="store_true",
        help="backfill every calendar selected in the Google Calendar UI",
    )
    canvas_sync = subcommands.add_parser("canvas-sync", help="read-sync current and historical Canvas assignments")
    canvas_sync.add_argument("--base-url", required=True, help="your school Canvas HTTPS URL")
    canvas_sync.add_argument("--secret-name", default="canvas-api-token")
    canvas_ical_sync = subcommands.add_parser(
        "canvas-ical-sync",
        help="read-sync a private Canvas Calendar Feed URL from the operating-system keyring",
    )
    canvas_ical_sync.add_argument("--secret-name", default="canvas-ical-feed-url")
    canvas_ical_setup = subcommands.add_parser(
        "canvas-ical-setup",
        help="securely prompt for, validate, save, and first-sync a private Canvas Calendar Feed URL",
    )
    canvas_ical_setup.add_argument("--secret-name", default="canvas-ical-feed-url")
    health_sync = subcommands.add_parser(
        "health-sync", help="read-sync Google Health steps/sleep/heart-rate; reuses the google-auth grant"
    )
    health_sync.add_argument("--lookback-days", type=int, default=14, help="how many days back to fetch each sync")
    github_sync = subcommands.add_parser("github-sync", help="read-sync unread GitHub notifications")
    github_sync.add_argument("--secret-name", default="github-token")
    github_issue_propose = subcommands.add_parser(
        "github-issue-propose", help="preview creating a GitHub issue; nothing is posted yet"
    )
    github_issue_propose.add_argument("--actor", required=True)
    github_issue_propose.add_argument("--repository", required=True, help="explicit owner/repository target")
    github_issue_propose.add_argument("--title", required=True)
    github_issue_propose.add_argument("--body")
    github_issue_execute = subcommands.add_parser(
        "github-issue-execute", help="consume a fresh approval token and create the previewed GitHub issue"
    )
    github_issue_execute.add_argument("--approval-id", required=True)
    github_issue_execute.add_argument("--actor", required=True)
    github_issue_execute.add_argument("--token", required=True)
    github_issue_execute.add_argument("--secret-name", default="github-issue-token")
    pr_propose = subcommands.add_parser("github-pr-comment-propose", help="preview a GitHub PR conversation comment")
    pr_propose.add_argument("--actor", required=True); pr_propose.add_argument("--repository", required=True); pr_propose.add_argument("--pull-number", required=True, type=int); pr_propose.add_argument("body")
    pr_execute = subcommands.add_parser("github-pr-comment-execute", help="post an approved GitHub PR comment")
    pr_execute.add_argument("--approval-id", required=True); pr_execute.add_argument("--actor", required=True); pr_execute.add_argument("--token", required=True); pr_execute.add_argument("--secret-name", default="github-pr-token")
    gmail_sync = subcommands.add_parser("gmail-sync", help="read-sync unread Gmail inbox headers and snippets")
    gmail_sync.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_UNREAD_LIMIT,
        help="max unread messages to sync, most recent first; bounded so a large backlog doesn't mean thousands of API calls every cycle",
    )
    gmail_inbound_poll = subcommands.add_parser(
        "gmail-inbound-poll", help="turn 'Task:'/'Remind:' subject commands from allowed senders into local tasks"
    )
    gmail_inbound_poll.add_argument(
        "--sender", action="append", required=True, help="locally allowed command sender email address"
    )
    gmail_inbound_poll.add_argument(
        "--destination", help="channel:recipient to deliver 'Remind:' reminders to; omit to create the task without one"
    )
    calendar_propose = subcommands.add_parser(
        "calendar-event-propose", help="preview a calendar event write; nothing is sent to Google yet"
    )
    calendar_propose.add_argument("--actor", required=True)
    calendar_propose.add_argument("--calendar-id", default="primary")
    calendar_propose.add_argument("--summary", required=True)
    calendar_propose.add_argument("--start", required=True, help="ISO-8601 time with timezone")
    calendar_propose.add_argument("--end", required=True, help="ISO-8601 time with timezone")
    calendar_execute = subcommands.add_parser(
        "calendar-event-execute", help="consume a fresh approval token and create the previewed event"
    )
    calendar_execute.add_argument("--approval-id", required=True)
    calendar_execute.add_argument("--actor", required=True)
    calendar_execute.add_argument("--token", required=True)
    gmail_draft_propose = subcommands.add_parser(
        "gmail-draft-propose", help="preview a Gmail draft; nothing is sent to Gmail yet"
    )
    gmail_draft_propose.add_argument("--actor", required=True)
    gmail_draft_propose.add_argument("--to", required=True)
    gmail_draft_propose.add_argument("--subject", required=True)
    gmail_draft_propose.add_argument("body")
    gmail_draft_execute = subcommands.add_parser(
        "gmail-draft-execute", help="consume a fresh approval token and create the previewed draft"
    )
    gmail_draft_execute.add_argument("--approval-id", required=True)
    gmail_draft_execute.add_argument("--actor", required=True)
    gmail_draft_execute.add_argument("--token", required=True)
    gmail_send_propose = subcommands.add_parser("gmail-send-propose", help="preview sending Gmail; nothing is sent yet")
    gmail_send_propose.add_argument("--actor", required=True)
    gmail_send_propose.add_argument("--to", required=True)
    gmail_send_propose.add_argument("--subject", required=True)
    gmail_send_propose.add_argument("body")
    gmail_send_execute = subcommands.add_parser("gmail-send-execute", help="send a Gmail message after explicit approval")
    gmail_send_execute.add_argument("--approval-id", required=True)
    gmail_send_execute.add_argument("--actor", required=True)
    gmail_send_execute.add_argument("--token", required=True)
    run = subcommands.add_parser("run", help="run Alfred continuously: paired message channels, due jobs, and connector sync")
    run.add_argument("--pair", action="append", default=[], help="locally paired CHAT_ID:USER_ID; enables Telegram intake")
    run.add_argument(
        "--chat-id", action="append", type=int, default=[], help="locally allowed delivery chat ID; enables Telegram delivery"
    )
    run.add_argument("--telegram-secret-name", default="telegram-bot-token")
    run.add_argument("--slack-pair", action="append", default=[], help="locally paired Slack CHANNEL_ID:USER_ID; enables Slack intake")
    run.add_argument("--slack-channel-id", action="append", default=[], help="locally allowed Slack delivery channel ID")
    run.add_argument("--slack-app-secret-name", default="slack-app-token")
    run.add_argument("--slack-bot-secret-name", default="slack-bot-token")
    run.add_argument("--calendar-id", default="primary")
    run.add_argument(
        "--calendar-history-days",
        type=int,
        default=1095,
        help="past Calendar days to retain for memory; use 0 to disable (default: 1095)",
    )
    run.add_argument(
        "--calendar-history-interval",
        type=float,
        default=604800.0,
        help="minimum seconds between full Calendar history refreshes (default: weekly)",
    )
    run.add_argument("--canvas-base-url", help="enables Canvas sync when set")
    run.add_argument("--canvas-secret-name", default="canvas-api-token")
    run.add_argument(
        "--canvas-history-interval",
        type=float,
        default=86400.0,
        help="minimum seconds between full Canvas course-history reads (default: daily)",
    )
    run.add_argument(
        "--canvas-ical",
        action="store_true",
        help="enables direct read-only Canvas Calendar Feed sync from the operating-system keyring",
    )
    run.add_argument("--canvas-ical-secret-name", default="canvas-ical-feed-url")
    run.add_argument(
        "--canvas-ical-interval",
        type=float,
        default=900.0,
        help="minimum seconds between direct Canvas Calendar Feed refreshes (default: 900)",
    )
    run.add_argument("--github-secret-name", default="github-token")
    run.add_argument(
        "--google-health",
        action="store_true",
        help="enables Google Health steps/sleep/heart-rate sync (reuses the google-auth grant; needs its health scopes)",
    )
    run.add_argument("--google-health-lookback-days", type=int, default=14)
    run.add_argument(
        "--gmail-inbound-sender",
        action="append",
        default=[],
        help="locally allowed command sender email address; enables inbound email intake",
    )
    run.add_argument(
        "--gmail-inbound-destination",
        help="channel:recipient to deliver 'Remind:' email reminders to; omit to create the task without one",
    )
    run.add_argument(
        "--gmail-unread-limit",
        type=int,
        default=50,
        help=(
            "max unread Gmail messages per sync in the run loop (default 50, lower than the "
            "one-shot gmail-sync command's 500): each sync blocks this single-threaded loop, "
            "and 500 measured at 45s against 7s for 50"
        ),
    )
    run.add_argument(
        "--hermes-profile",
        help=(
            "Hermes profile name; enables answering free-form Telegram messages with the agent "
            "instead of replying with the /task|/remind help text"
        ),
    )
    run.add_argument(
        "--hermes-command",
        default="hermes",
        help="hermes executable to invoke; use a full path when PATH differs (e.g. under the Windows service)",
    )
    run.add_argument(
        "--hermes-timeout",
        type=float,
        default=60.0,
        help="seconds to allow one agent turn before giving up on it",
    )
    run.add_argument(
        "--embedding-model",
        help="local Ollama embedding model for hybrid memory recall (for example nomic-embed-text)",
    )
    run.add_argument(
        "--hermes-monthly-call-limit",
        type=int,
        default=1000,
        help="hard monthly cap on external Hermes turns; local direct answers do not count",
    )
    run.add_argument("--vault", type=Path, help="enables periodic vault import when set")
    run.add_argument("--poll-timeout", type=int, default=20, help="seconds per Telegram long-poll cycle")
    run.add_argument("--idle-sleep", type=float, default=5.0, help="seconds to rest between cycles")
    run.add_argument("--connector-interval", type=float, default=900.0, help="minimum seconds between each connector sync")
    run.add_argument("--iterations", type=int, help="stop after N cycles instead of running forever (mainly for testing)")
    service_configure = subcommands.add_parser(
        "service-configure",
        help="store the 'alfred run ...' arguments the Windows service (alfred.winservice) will launch",
    )
    service_configure.add_argument(
        "run_args",
        nargs=argparse.REMAINDER,
        help="everything after this is passed through verbatim, e.g. run --pair 123:456 --chat-id 123",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = database_from_args(args)
    if args.command == "init":
        print(json.dumps({"schema_version": database.migrate(), "database_path": str(database.path)}))
        return 0
    if args.command == "status":
        print(json.dumps(database.status()))
        return 0
    if args.command == "academic-memory-rebuild":
        rollup = AcademicMemoryService(database).rebuild_if_changed()
        history = HistoricalMemoryService(database).rebuild_if_changed()
        print(
            json.dumps(
                {
                    "rollup": rollup.model_dump(mode="json"),
                    "history": history.model_dump(mode="json"),
                }
            )
        )
        return 0
    if args.command == "connector-status":
        print(json.dumps([health.model_dump(mode="json") for health in connector_health(database)]))
        return 0
    if args.command == "vault-sync-status":
        print(check_couchdb(args.url).model_dump_json())
        return 0
    if args.command == "backup-create":
        receipt = EncryptedBackupService(database, ApprovalService(database)).create(
            args.output, encoded_key=SystemKeyringSecretStore().get_required(args.secret_name)
        )
        print(receipt.model_dump_json())
        return 0
    if args.command == "backup-key-generate":
        secrets = SystemKeyringSecretStore()
        try:
            secrets.get_required(args.secret_name)
        except SecretStoreError:
            secrets.store(args.secret_name, EncryptedBackupService.generate_key())
            print(json.dumps({"secret_name": args.secret_name, "created": True}))
            return 0
        raise SystemExit("backup key already exists; refusing to overwrite it")
    if args.command == "backup-restore-propose":
        print(EncryptedBackupService(database, ApprovalService(database)).propose_restore(args.backup, actor=args.actor).model_dump_json())
        return 0
    if args.command == "backup-restore-execute":
        receipt = EncryptedBackupService(database, ApprovalService(database)).execute_restore(
            args.approval_id, actor=args.actor, token=args.token,
            encoded_key=SystemKeyringSecretStore().get_required(args.secret_name),
        )
        print(receipt.model_dump_json())
        return 0
    audit_log = AuditLog(database)
    if args.command == "audit":
        try:
            arguments = json.loads(args.arguments)
            result = json.loads(args.result)
        except json.JSONDecodeError as error:
            raise SystemExit(f"invalid JSON: {error.msg}") from error
        if not isinstance(arguments, dict) or not isinstance(result, dict):
            raise SystemExit("--arguments and --result must be JSON objects")
        record_id = audit_log.append(
            AuditEvent(
                actor=args.actor,
                client=args.client,
                tool=args.tool,
                outcome=args.outcome,
                arguments=arguments,
                result=result,
                correlation_id=args.correlation_id,
            )
        )
        print(json.dumps({"audit_record_id": record_id}))
        return 0
    if args.command == "audit-verify":
        valid = audit_log.verify()
        print(json.dumps({"valid": valid}))
        return 0 if valid else 1
    if args.command == "telegram-handle":
        try:
            update_json = args.update_file.read_text(encoding="utf-8") if args.update_file else args.update
            update = TelegramUpdate.model_validate_json(update_json)
        except ValueError as error:
            raise SystemExit(f"invalid Telegram update JSON: {error}") from error
        gateway = TelegramGateway(database, {TelegramPair(chat_id=args.chat_id, user_id=args.user_id)})
        receipt = gateway.handle(update)
        print(receipt.model_dump_json())
        return 0
    if args.command == "run-due":
        now = _parse_timestamp(args.now) if args.now else None
        executed = JobRunner(database).run_due(now)
        print(json.dumps({"processed": [item.model_dump(mode="json") for item in executed]}))
        return 0
    if args.command == "brief":
        now = _parse_timestamp(args.now) if args.now else None
        print(BriefingService(database).morning_brief(now).render())
        return 0
    if args.command == "schedule-brief":
        try:
            hour, minute = (int(value) for value in args.at.split(":"))
            local_time = time(hour, minute)
        except ValueError as error:
            raise SystemExit("--at must be a valid HH:MM local time") from error
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                job_id = create_daily(
                    connection,
                    destination=args.destination or f"telegram:{args.chat_id}",
                    local_time=local_time,
                    timezone_name=args.timezone,
                )
        print(json.dumps({"job_id": job_id}))
        return 0
    if args.command == "task-upsert":
        due_at = _parse_timestamp(args.due_at) if args.due_at else UNSET
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                if args.task_id is None:
                    event = EventStore.append(
                        connection,
                        source="cli",
                        external_id=f"task:{uuid4()}",
                        occurred_at=datetime.now(UTC),
                        content=args.title,
                        metadata={},
                    )
                    task = TaskStore.upsert(connection, title=args.title, due_at=due_at, source_event_id=event.id)
                else:
                    task = TaskStore.upsert(connection, task_id=args.task_id, title=args.title, due_at=due_at)
        print(task.model_dump_json())
        return 0
    if args.command == "task-complete":
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                task = TaskStore.complete(connection, args.task_id)
        print(task.model_dump_json())
        return 0
    if args.command == "reminder-set":
        run_at = _parse_timestamp(args.run_at)
        database.migrate()
        with database.connect() as connection:
            with database.transaction(connection):
                if args.task_id is None:
                    event = EventStore.append(
                        connection,
                        source="cli",
                        external_id=f"reminder:{uuid4()}",
                        occurred_at=datetime.now(UTC),
                        content=args.text,
                        metadata={},
                    )
                    task = TaskStore.upsert(connection, title=args.text, due_at=run_at, source_event_id=event.id)
                    task_id = task.id
                else:
                    task_id = args.task_id
                job = ReminderStore.create(
                    connection,
                    run_at=run_at,
                    task_id=task_id,
                    destination=args.destination or f"telegram:{args.chat_id}",
                    text=args.text,
                    idempotency_key=f"reminder:{task_id}:{run_at.isoformat()}",
                )
        print(job.model_dump_json())
        return 0
    if args.command == "memory-embed-backfill":
        provider = OllamaEmbeddingProvider(model_name=args.model, base_url=args.base_url)
        count = EmbeddingBackfill(database, provider).run(limit=args.limit)
        print(json.dumps({"model": args.model, "embedded": count}))
        return 0
    graph = MemoryGraph(database)
    if args.command == "memory-self":
        print(graph.ensure_self(args.label).model_dump_json())
        return 0
    if args.command == "memory-entity":
        print(graph.create_entity(entity_type=args.type, label=args.label, domains=args.domain).model_dump_json())
        return 0
    if args.command == "memory-relation":
        valid_from = _parse_timestamp(args.valid_from) if args.valid_from else None
        print(
            graph.add_relationship(
                source_entity_id=args.source_id,
                predicate=args.predicate,
                target_entity_id=args.target_id,
                relation_kind=args.kind,
                cardinality=args.cardinality,
                valid_from=valid_from,
                domains=args.domain,
            ).model_dump_json()
        )
        return 0
    if args.command == "memory-alias":
        print(graph.add_alias(args.entity_id, args.alias).model_dump_json())
        return 0
    if args.command == "remember":
        print(graph.remember(args.statement, kind=args.kind).model_dump_json())
        return 0
    if args.command == "memory-search":
        print(graph.search(args.query).model_dump_json())
        return 0
    if args.command == "memory-correct":
        print(graph.supersede_memory(args.memory_id, args.statement).model_dump_json())
        return 0
    if args.command == "vault-export-entity":
        print(VaultProjector(database, args.vault).project_entity(args.entity_id).model_dump_json())
        return 0
    if args.command == "vault-export-memory":
        print(VaultProjector(database, args.vault).project_memory(args.memory_id).model_dump_json())
        return 0
    if args.command == "vault-export-source-event":
        print(VaultProjector(database, args.vault).export_by_source_event(args.source_event_id).model_dump_json())
        return 0
    if args.command == "vault-import":
        print(VaultImporter(database, args.vault).sync().model_dump_json())
        return 0
    if args.command == "client-grant":
        print(
            PolicyStore(database).grant(
                client_id=args.client_id,
                allowed_sensitivities=set(args.sensitivity),
                allowed_tools=set(args.tool),
                allow_write=args.allow_write,
            ).model_dump_json()
        )
        return 0
    if args.command == "mcp-http-token-generate":
        secrets_store = SystemKeyringSecretStore()
        try:
            secrets_store.get_required(args.secret_name)
        except SecretStoreError:
            secrets_store.store(args.secret_name, generate_http_token())
            print(json.dumps({"secret_name": args.secret_name, "created": True}))
            return 0
        raise SystemExit("MCP HTTP bearer token already exists; refusing to overwrite it")
    if args.command == "mcp-http-run":
        token = SystemKeyringSecretStore().get_required(args.secret_name)
        run_streamable_http(database.path, client_id=args.client_id, port=args.port, bearer_token=token)
        return 0
    if args.command == "admin-ui-token-generate":
        secrets_store = SystemKeyringSecretStore()
        try:
            secrets_store.get_required(args.secret_name)
        except SecretStoreError:
            secrets_store.store(args.secret_name, generate_http_token())
            print(json.dumps({"secret_name": args.secret_name, "created": True}))
            return 0
        raise SystemExit("admin UI bearer token already exists; refusing to overwrite it")
    if args.command == "admin-ui-run":
        token = SystemKeyringSecretStore().get_required(args.secret_name)
        run_admin_ui(database, port=args.port, bearer_token_value=token, host=args.host)
        return 0
    approvals = ApprovalService(database)
    if args.command == "approval-propose":
        try:
            preview = json.loads(args.preview)
        except json.JSONDecodeError as error:
            raise SystemExit(f"invalid preview JSON: {error.msg}") from error
        if not isinstance(preview, dict):
            raise SystemExit("--preview must be a JSON object")
        print(approvals.propose(actor=args.actor, action_type=args.action_type, preview=preview).model_dump_json())
        return 0
    if args.command == "approval-approve":
        print(approvals.approve(args.approval_id, actor=args.actor).model_dump_json())
        return 0
    if args.command == "approval-consume":
        print(approvals.consume(args.approval_id, actor=args.actor, token=args.token).model_dump_json())
        return 0
    if args.command == "telegram-poll":
        client = TelegramBotClient(SystemKeyringSecretStore().get_required(args.secret_name))
        try:
            result = TelegramLongPoller(database, client, {_parse_telegram_pair(value) for value in args.pair}).poll_once(
                timeout_seconds=args.timeout
            )
        finally:
            client.close()
        print(result.model_dump_json())
        return 0
    if args.command == "telegram-deliver":
        client = TelegramBotClient(SystemKeyringSecretStore().get_required(args.secret_name))
        try:
            result = TelegramOutboxWorker(database, client, set(args.chat_id)).deliver_pending(limit=args.limit)
        finally:
            client.close()
        print(json.dumps([item.model_dump(mode="json") for item in result]))
        return 0
    if args.command == "run":
        with running_alfred_runner(database, args) as runner:
            try:
                runner.run_forever(iterations=args.iterations)
            except KeyboardInterrupt:
                print("\n[alfred run] stopped")
        return 0
    if args.command == "service-configure":
        config_path = configure_windows_service(args.run_args)
        print(json.dumps({"config_path": str(config_path), "args": args.run_args}))
        return 0
    if args.command == "google-auth":
        secret_store = SystemKeyringSecretStore()
        client_id = secret_store.get_required("google-oauth-client-id")
        client_secret = secret_store.get_required("google-oauth-client-secret")
        scopes = tuple(args.scope) if args.scope else DEFAULT_SCOPES
        token = authorize_interactively(
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
            port=args.port,
            timeout_seconds=args.timeout,
            open_browser=not args.no_browser,
            on_url=lambda url: print(f"Open this URL to authorize Alfred:\n{url}"),
        )
        if not token.refresh_token:
            raise SystemExit(
                "Google did not return a refresh token. Revoke Alfred's prior access at "
                "https://myaccount.google.com/permissions and run 'alfred google-auth' again."
            )
        secret_store.store("google-oauth-refresh-token", token.refresh_token)
        print(json.dumps({"granted_scopes": token.scope, "refresh_token_stored": True}))
        return 0
    if args.command == "calendar-sync":
        if not 1 <= args.days <= 90:
            raise SystemExit("--days must be between 1 and 90")
        start, _ = default_sync_window()
        client = GoogleCalendarClient(_google_access_token())
        try:
            result = GoogleCalendarSync(database, client).sync(
                calendar_id=args.calendar_id,
                time_min=start,
                time_max=start + timedelta(days=args.days),
            )
        finally:
            client.close()
        print(result.model_dump_json())
        return 0
    if args.command == "calendar-history-sync":
        if not 1 <= args.days <= 3650:
            raise SystemExit("--days must be between 1 and 3650")
        end = datetime.now(UTC)
        client = GoogleCalendarClient(_google_access_token())
        try:
            if args.all_selected:
                calendars = CalendarCatalogSync(database, client).sync()
                calendar_ids = ["primary" if item["primary"] else str(item["id"]) for item in calendars]
            else:
                calendar_ids = [args.calendar_id]
            results = []
            skipped = 0
            for calendar_id in calendar_ids:
                try:
                    results.append(
                        GoogleCalendarHistorySync(database, client).sync(
                            calendar_id=calendar_id,
                            time_min=end - timedelta(days=args.days),
                            time_max=end,
                        )
                    )
                except Exception:
                    if not args.all_selected:
                        raise
                    skipped += 1
        finally:
            client.close()
        print(
            json.dumps(
                {
                    "calendars_synced": len(results),
                    "calendars_skipped": skipped,
                    "received": sum(result.received for result in results),
                    "stored": sum(result.stored for result in results),
                }
            )
        )
        return 0
    if args.command == "canvas-sync":
        client = CanvasClient(args.base_url, SystemKeyringSecretStore().get_required(args.secret_name))
        try:
            result = CanvasSync(database, client, include_history=True).sync()
        finally:
            client.close()
        print(result.model_dump_json())
        return 0
    if args.command == "canvas-ical-sync":
        result = _canvas_ical_sync_once(database, args.secret_name)
        print(result.model_dump_json())
        return 0
    if args.command == "canvas-ical-setup":
        feed_url = getpass.getpass(
            "Paste the private Canvas Calendar Feed URL, then press Enter "
            "(input is hidden for security): "
        )
        result = setup_canvas_ical_feed(
            database,
            SystemKeyringSecretStore(),
            feed_url,
            secret_name=args.secret_name,
        )
        print(result.model_dump_json())
        return 0
    if args.command == "health-sync":
        client = GoogleHealthClient(_google_access_token())
        try:
            result = GoogleHealthSync(database, client, lookback_days=args.lookback_days).sync()
        finally:
            client.close()
        print(result.model_dump_json())
        return 0
    if args.command == "github-sync":
        client = GitHubClient(SystemKeyringSecretStore().get_required(args.secret_name))
        try:
            result = GitHubNotificationsSync(database, client).sync()
        finally:
            client.close()
        print(result.model_dump_json())
        return 0
    if args.command == "github-issue-propose":
        approval = GitHubActions(database, approvals).propose_issue(
            actor=args.actor, repository=args.repository, title=args.title, body=args.body
        )
        print(approval.model_dump_json())
        return 0
    if args.command == "github-issue-execute":
        client = GitHubClient(SystemKeyringSecretStore().get_required(args.secret_name))
        try:
            receipt = GitHubActions(database, approvals, client).execute(args.approval_id, actor=args.actor, token=args.token)
        finally:
            client.close()
        print(receipt.model_dump_json())
        return 0
    if args.command == "github-pr-comment-propose":
        print(GitHubActions(database, approvals).propose_pr_comment(actor=args.actor, repository=args.repository, pull_number=args.pull_number, body=args.body).model_dump_json())
        return 0
    if args.command == "github-pr-comment-execute":
        client = GitHubClient(SystemKeyringSecretStore().get_required(args.secret_name))
        try: receipt = GitHubActions(database, approvals, client).execute_pr_comment(args.approval_id, actor=args.actor, token=args.token)
        finally: client.close()
        print(receipt.model_dump_json())
        return 0
    if args.command == "gmail-sync":
        client = GmailClient(_google_access_token())
        try:
            result = GmailSync(database, client, limit=args.limit).sync()
        finally:
            client.close()
        print(result.model_dump_json())
        return 0
    if args.command == "gmail-inbound-poll":
        client = GmailClient(_google_access_token())
        try:
            result = GmailInboundGateway(
                database, client, set(args.sender), default_reminder_destination=args.destination
            ).poll()
        finally:
            client.close()
        print(result.model_dump_json())
        return 0
    if args.command == "calendar-event-propose":
        start = _parse_timestamp(args.start)
        end = _parse_timestamp(args.end)
        # No Google credential is touched here; proposing is pure local bookkeeping.
        actions = GoogleCalendarActions(database, approvals)
        print(
            actions.propose_event(
                actor=args.actor, calendar_id=args.calendar_id, summary=args.summary, start=start, end=end
            ).model_dump_json()
        )
        return 0
    if args.command == "calendar-event-execute":
        client = GoogleCalendarClient(_google_access_token())
        try:
            receipt = GoogleCalendarActions(database, approvals, client).execute(
                args.approval_id, actor=args.actor, token=args.token
            )
        finally:
            client.close()
        print(receipt.model_dump_json())
        return 0
    if args.command == "gmail-draft-propose":
        # No Gmail credential is touched here; proposing is pure local bookkeeping.
        actions = GmailActions(database, approvals)
        print(
            actions.propose_draft(actor=args.actor, to=args.to, subject=args.subject, body=args.body).model_dump_json()
        )
        return 0
    if args.command == "gmail-draft-execute":
        client = GmailClient(_google_access_token())
        try:
            receipt = GmailActions(database, approvals, client).execute(args.approval_id, actor=args.actor, token=args.token)
        finally:
            client.close()
        print(receipt.model_dump_json())
        return 0
    if args.command == "gmail-send-propose":
        print(GmailSendActions(database, approvals).propose_send(
            actor=args.actor, to=args.to, subject=args.subject, body=args.body
        ).model_dump_json())
        return 0
    if args.command == "gmail-send-execute":
        client = GmailClient(_google_access_token())
        try:
            receipt = GmailSendActions(database, approvals, client).execute(args.approval_id, actor=args.actor, token=args.token)
        finally:
            client.close()
        print(receipt.model_dump_json())
        return 0
    if args.command == "memory-forget-propose":
        proposal = MemoryActions(database, approvals).propose_forget(args.memory_id, actor=args.actor, reason=args.reason)
        print(proposal.model_dump_json())
        return 0
    if args.command == "memory-forget-execute":
        receipt = MemoryActions(database, approvals).execute_forget(args.approval_id, actor=args.actor, token=args.token)
        print(receipt.model_dump_json())
        return 0
    if args.command == "memory-forget-source-propose":
        proposal = MemoryActions(database, approvals).propose_forget_by_source_event(
            args.source_event_id, actor=args.actor, reason=args.reason
        )
        print(proposal.model_dump_json())
        return 0
    if args.command == "memory-forget-source-execute":
        receipt = MemoryActions(database, approvals).execute_forget_by_source_event(
            args.approval_id, actor=args.actor, token=args.token
        )
        print(receipt.model_dump_json())
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


def _google_access_token() -> str:
    return current_access_token(SystemKeyringSecretStore())


def _calendar_sync_once(database: Database, calendar_id: str) -> None:
    client = GoogleCalendarClient(_google_access_token())
    try:
        # The bounds are ignored by Google when a valid incremental cursor is
        # present. If Google does not issue one (or invalidates it), they keep
        # the fallback sync useful and fast instead of downloading the user's
        # entire calendar history every cycle.
        start, end = default_sync_window()
        if calendar_id != "primary":
            calendar_ids = [calendar_id]
        else:
            calendars = CalendarCatalogSync(database, client).sync()
            calendar_ids = [
                "primary" if item["primary"] else str(item["id"])
                for item in calendars
            ]
        for selected_id in calendar_ids:
            try:
                GoogleCalendarSync(database, client).sync(
                    calendar_id=selected_id, time_min=start, time_max=end
                )
            except Exception:
                # Calendar subscriptions are independent. A public holiday or
                # stale shared calendar can remain visible in CalendarList but
                # reject events.list. GoogleCalendarSync has already recorded
                # that account's error; continue so it cannot prevent the
                # user's other selected calendars from becoming current.
                continue
    finally:
        client.close()


def _calendar_history_sync_once(
    database: Database,
    calendar_id: str,
    *,
    days: int,
    minimum_age_seconds: float,
) -> None:
    if not 1 <= days <= 3650:
        raise ValueError("calendar history days must be between 1 and 3650")
    client = GoogleCalendarClient(_google_access_token())
    try:
        if calendar_id != "primary":
            calendar_ids = [calendar_id]
        else:
            calendars = CalendarCatalogSync(database, client).sync()
            calendar_ids = ["primary" if item["primary"] else str(item["id"]) for item in calendars]
        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        for selected_id in calendar_ids:
            if _sync_state_is_fresh(
                database,
                connector="google_calendar_history",
                account=selected_id,
                minimum_age_seconds=minimum_age_seconds,
            ):
                continue
            try:
                GoogleCalendarHistorySync(database, client).sync(
                    calendar_id=selected_id, time_min=start, time_max=end
                )
            except Exception:
                continue
    finally:
        client.close()


def _sync_state_is_fresh(
    database: Database, *, connector: str, account: str, minimum_age_seconds: float
) -> bool:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT last_success_at FROM sync_state WHERE connector = ? AND account = ?",
            (connector, account),
        ).fetchone()
    if row is None or not row["last_success_at"]:
        return False
    last_success = datetime.fromisoformat(str(row["last_success_at"]).replace("Z", "+00:00"))
    return datetime.now(UTC) - last_success.astimezone(UTC) < timedelta(seconds=minimum_age_seconds)


def _canvas_sync_once(
    database: Database, base_url: str, secret_name: str, *, include_history: bool = False
) -> None:
    client = CanvasClient(base_url, SystemKeyringSecretStore().get_required(secret_name))
    try:
        CanvasSync(database, client, include_history=include_history).sync()
    finally:
        client.close()


def _canvas_ical_sync_once(database: Database, secret_name: str) -> CanvasICalSyncResult:
    client = CanvasICalClient(SystemKeyringSecretStore().get_required(secret_name))
    try:
        return CanvasICalSync(database, client).sync()
    finally:
        client.close()


def _health_sync_once(database: Database, lookback_days: int) -> None:
    client = GoogleHealthClient(_google_access_token())
    try:
        GoogleHealthSync(database, client, lookback_days=lookback_days).sync()
    finally:
        client.close()


def _github_sync_once(database: Database, secret_name: str) -> None:
    client = GitHubClient(SystemKeyringSecretStore().get_required(secret_name))
    try:
        GitHubNotificationsSync(database, client).sync()
    finally:
        client.close()


def _gmail_sync_once(database: Database, limit: int = DEFAULT_UNREAD_LIMIT) -> None:
    client = GmailClient(_google_access_token())
    try:
        GmailSync(database, client, limit=limit).sync()
    finally:
        client.close()


def _gmail_inbound_poll_once(
    database: Database, senders: set[str], destination: str | None, limit: int = DEFAULT_UNREAD_LIMIT
) -> None:
    client = GmailClient(_google_access_token())
    try:
        GmailInboundGateway(
            database, client, senders, default_reminder_destination=destination, limit=limit
        ).poll()
    finally:
        client.close()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise SystemExit("--now must include a timezone")
    return parsed


def _parse_telegram_pair(value: str) -> TelegramPair:
    chat_id, separator, user_id = value.partition(":")
    if not separator:
        raise SystemExit("--pair must be CHAT_ID:USER_ID")
    try:
        return TelegramPair(chat_id=int(chat_id), user_id=int(user_id))
    except ValueError as error:
        raise SystemExit("--pair must be CHAT_ID:USER_ID") from error


def _parse_slack_pair(value: str) -> SlackPair:
    channel_id, separator, user_id = value.partition(":")
    if not separator or not channel_id or not user_id:
        raise SystemExit("--slack-pair must be CHANNEL_ID:USER_ID")
    if not channel_id.isalnum() or not user_id.isalnum():
        raise SystemExit("--slack-pair IDs must be alphanumeric Slack IDs")
    return SlackPair(channel_id=channel_id, user_id=user_id)


if __name__ == "__main__":
    raise SystemExit(main())
