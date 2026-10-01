"""`opendot eval` argument parsing and dispatch."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .runner import StackUnavailable, UnknownScenario, format_result, format_summary, run_scenarios, select


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "eval",
        help="run the scenario suite (ARCHITECTURE.md section 14, M2) against a scripted fake model",
    )
    parser.add_argument("--suite", default="core", help="scenario suite to run (default: core)")
    parser.add_argument("--only", help="comma-separated scenario ids to run, for example S3,S5")


def run(args: argparse.Namespace, *, out: Any = None, err: Any = None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    only = [item for item in (args.only or "").split(",") if item.strip()]
    try:
        ids = select(args.suite, only)
    except UnknownScenario as error:
        print(f"opendot eval: {error}", file=err)
        return 2
    results = []
    try:
        for scenario_id in ids:
            result = run_scenarios([scenario_id])[0]
            results.append(result)
            print(format_result(result), file=out)
    except StackUnavailable as error:
        print(
            f"opendot eval: cannot run the {args.suite} suite yet: {error}. "
            "Build the missing M2 module first (docs/m2-design.md).",
            file=err,
        )
        return 2
    print(format_summary(args.suite, results), file=out)
    return 0 if all(result.passed for result in results) else 1
