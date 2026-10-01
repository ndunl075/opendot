"""`opendot routines list|run|enable|disable` argument parsing and dispatch."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ..db import Database
from .scheduler import RoutineScheduler


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("routines", help="list, run, enable or disable the built-in routines")
    actions = parser.add_subparsers(dest="routines_action", required=True)
    actions.add_parser("list", help="show each routine's switch, schedule and next run as JSON")
    run = actions.add_parser("run", help="run one routine now and queue its delivery (ignores schedule and quiet hours)")
    run.add_argument("name", help="morning_brief, inbox_triage or weekly_review")
    run.add_argument("--no-model", action="store_true", help="never call a model; deliver the plain-code result")
    for action, text in (("enable", "turn a routine on"), ("disable", "turn a routine off")):
        sub = actions.add_parser(action, help=text)
        sub.add_argument("name")


def run(args: argparse.Namespace, database: Database, *, scheduler: RoutineScheduler | None = None, out: Any = None) -> int:
    out = out or sys.stdout
    if scheduler is None:
        from ..connector_sync import pull_request_report
        from .runtime import build_routine_loop

        try:
            pull_requests = pull_request_report()
        except Exception:
            pull_requests = None
        scheduler = RoutineScheduler(
            database,
            loop_factory=None if getattr(args, "no_model", False) else lambda: build_routine_loop(database),
            pull_requests=pull_requests,
        )
    try:
        if args.routines_action == "list":
            rows = [
                {
                    "name": item.name,
                    "enabled": item.enabled,
                    "schedule": item.schedule,
                    "next_run_at": item.next_run_at.isoformat() if item.next_run_at else None,
                    "last_run_at": item.last_run_at.isoformat() if item.last_run_at else None,
                }
                for item in scheduler.status()
            ]
            out.write(json.dumps(rows, indent=2) + "\n")
            return 0
        if args.routines_action in {"enable", "disable"}:
            scheduler.configure(args.name, enabled=args.routines_action == "enable")
            out.write(f"{args.name}: {args.routines_action}d\n")
            return 0
        result = scheduler.run_named(args.name)
    except KeyError as error:
        sys.stderr.write(f"{error.args[0]}\n")
        return 2
    out.write(
        json.dumps(
            {
                "routine": result.routine,
                "status": result.status,
                "outbox_id": result.outbox_id,
                "model_called": result.model_called,
                "used_model": result.used_model,
                "fallback_reason": result.fallback_reason,
                "detail": result.detail,
            }
        )
        + "\n"
    )
    if result.text:
        out.write(result.text + "\n")
    return 1 if result.status == "error" else 0
