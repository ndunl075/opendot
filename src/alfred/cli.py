"""Small CLI that exercises the same core used by future transports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .audit import AuditEvent, AuditLog
from .config import Settings
from .db import Database


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
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
