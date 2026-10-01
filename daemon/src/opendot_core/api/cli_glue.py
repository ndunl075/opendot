"""CLI wiring for the contract tools, kept out of cli.py so that file stays a small insertion."""

from __future__ import annotations

import argparse
from typing import Any

from . import export, mock_server


def add_parsers(subcommands: Any) -> None:
    export.add_parser(subcommands)
    mock_server.add_parser(subcommands)


def dispatch(args: argparse.Namespace) -> int | None:
    """Return an exit code if ``args`` is a contract command, else None."""
    if args.command == "contract" and args.contract_command == "export":
        return export.run_export(args)
    if args.command == "mock-server":
        return mock_server.run_mock_server(args)
    return None
