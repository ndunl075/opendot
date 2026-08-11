"""Small CLI that exercises the same core used by future transports."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Sequence

from .audit import AuditEvent, AuditLog
from .config import Settings
from .db import Database
from .briefing import BriefingService
from .jobs import JobRunner
from .memory_graph import MemoryGraph
from .vault import VaultProjector
from .policy import ApprovalService, PolicyStore
from .secret_store import SystemKeyringSecretStore
from .google_calendar import GoogleCalendarClient, GoogleCalendarSync, default_sync_window
from .canvas import CanvasClient, CanvasSync
from .github import GitHubClient, GitHubNotificationsSync
from .brief_schedule import create_daily
from .telegram_bot import TelegramBotClient
from .telegram_runtime import TelegramLongPoller, TelegramOutboxWorker
from .telegram import TelegramGateway, TelegramPair, TelegramUpdate


def _database_from_args(args: argparse.Namespace) -> Database:
    settings = Settings.from_environment(Path(args.db) if args.db else None)
    return Database(settings.database_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alfred", description="Alfred Core local CLI")
    parser.add_argument("--db", help="SQLite database path; defaults to ALFRED_DB_PATH or .alfred/alfred.db")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("init", help="create or migrate the local database")
    subcommands.add_parser("status", help="show non-sensitive local status")
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
    schedule_brief = subcommands.add_parser("schedule-brief", help="schedule one local daily Telegram morning brief")
    schedule_brief.add_argument("--chat-id", required=True, type=int)
    schedule_brief.add_argument("--at", required=True, help="local 24-hour HH:MM time")
    schedule_brief.add_argument("--timezone", required=True, help="IANA timezone, e.g. America/New_York")
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
    remember = subcommands.add_parser("remember", help="store a confirmed local memory")
    remember.add_argument("statement")
    remember.add_argument("--kind", default="note")
    search = subcommands.add_parser("memory-search", help="search local memories and graph anchors")
    search.add_argument("query")
    correct = subcommands.add_parser("memory-correct", help="supersede a memory with a corrected statement")
    correct.add_argument("--memory-id", required=True)
    correct.add_argument("statement")
    forget = subcommands.add_parser("forget", help="scoped deletion of one local memory; evidence is kept")
    forget.add_argument("--memory-id", required=True)
    forget.add_argument("--reason", default="user requested deletion")
    export_entity = subcommands.add_parser("vault-export-entity", help="project one entity into local Markdown")
    export_entity.add_argument("--vault", type=Path, default=Path("alfred-vault"))
    export_entity.add_argument("--entity-id", required=True)
    export_memory = subcommands.add_parser("vault-export-memory", help="project one confirmed memory into local Markdown")
    export_memory.add_argument("--vault", type=Path, default=Path("alfred-vault"))
    export_memory.add_argument("--memory-id", required=True)
    grant = subcommands.add_parser("client-grant", help="grant a local client explicit scoped access")
    grant.add_argument("--client-id", required=True)
    grant.add_argument("--sensitivity", action="append", default=[])
    grant.add_argument("--tool", action="append", default=[])
    grant.add_argument("--allow-write", action="store_true")
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
    calendar_sync = subcommands.add_parser("calendar-sync", help="read-sync Google Calendar into local source events")
    calendar_sync.add_argument("--calendar-id", default="primary")
    calendar_sync.add_argument("--secret-name", default="google-calendar-access-token")
    calendar_sync.add_argument("--days", type=int, default=14, help="initial sync window length (1-90 days)")
    canvas_sync = subcommands.add_parser("canvas-sync", help="read-sync Canvas upcoming and missing assignments")
    canvas_sync.add_argument("--base-url", required=True, help="your school Canvas HTTPS URL")
    canvas_sync.add_argument("--secret-name", default="canvas-api-token")
    github_sync = subcommands.add_parser("github-sync", help="read-sync unread GitHub notifications")
    github_sync.add_argument("--secret-name", default="github-token")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = _database_from_args(args)
    if args.command == "init":
        print(json.dumps({"schema_version": database.migrate(), "database_path": str(database.path)}))
        return 0
    if args.command == "status":
        print(json.dumps(database.status()))
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
                job_id = create_daily(connection, chat_id=args.chat_id, local_time=local_time, timezone_name=args.timezone)
        print(json.dumps({"job_id": job_id}))
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
    if args.command == "remember":
        print(graph.remember(args.statement, kind=args.kind).model_dump_json())
        return 0
    if args.command == "memory-search":
        print(graph.search(args.query).model_dump_json())
        return 0
    if args.command == "memory-correct":
        print(graph.supersede_memory(args.memory_id, args.statement).model_dump_json())
        return 0
    if args.command == "forget":
        print(graph.forget_memory(args.memory_id, reason=args.reason).model_dump_json())
        return 0
    if args.command == "vault-export-entity":
        print(VaultProjector(database, args.vault).project_entity(args.entity_id).model_dump_json())
        return 0
    if args.command == "vault-export-memory":
        print(VaultProjector(database, args.vault).project_memory(args.memory_id).model_dump_json())
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
    if args.command == "calendar-sync":
        if not 1 <= args.days <= 90:
            raise SystemExit("--days must be between 1 and 90")
        start, _ = default_sync_window()
        client = GoogleCalendarClient(SystemKeyringSecretStore().get_required(args.secret_name))
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
    if args.command == "canvas-sync":
        client = CanvasClient(args.base_url, SystemKeyringSecretStore().get_required(args.secret_name))
        try:
            result = CanvasSync(database, client).sync()
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
    raise AssertionError(f"Unhandled command: {args.command}")


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


if __name__ == "__main__":
    raise SystemExit(main())
