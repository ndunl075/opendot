"""`opendot usage` argument parsing and dispatch."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from ..db import Database
from .meter import UsageMeter


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("usage", help="print credit usage by task, day and job type as JSON")
    parser.add_argument("--days", type=int, default=7, help="how many days to include (default 7)")


def run(args: argparse.Namespace, database: Database, *, out: Any = None) -> int:
    out = out or sys.stdout
    database.migrate()
    summary = UsageMeter(database).summary(days=args.days)
    out.write(summary.model_dump_json(indent=2) + "\n")
    return 0
