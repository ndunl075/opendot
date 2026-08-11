"""Small CLI that exercises the same core used by future transports."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .audit import AuditEvent, AuditLog
from .config import Settings
from .db import Database
from .briefing import BriefingService
from .jobs import JobRunner
from .memory_graph import MemoryGraph
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
    raise AssertionError(f"Unhandled command: {args.command}")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise SystemExit("--now must include a timezone")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
