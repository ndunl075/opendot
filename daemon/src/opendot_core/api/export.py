"""``opendot contract export``: deterministic JSON Schemas plus an endpoint/event index."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .contract import ENDPOINTS, STREAM, all_models, event_type, request_models
from .events import ClientFrame, StreamEvent
from .models import API_VERSION

UNIONS: dict[str, Any] = {"ClientFrame": ClientFrame, "StreamEvent": StreamEvent}


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` (default: the current directory, then this file) to ARCHITECTURE.md."""
    candidates = [start] if start else [Path.cwd(), Path(__file__).resolve()]
    for origin in candidates:
        for directory in [origin, *origin.parents]:
            if (directory / "ARCHITECTURE.md").is_file():
                return directory
    raise FileNotFoundError("could not find the repository root (a directory containing ARCHITECTURE.md)")


def dump_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def build_files() -> dict[str, str]:
    """Relative path (POSIX) -> file text for the whole contract."""
    files: dict[str, str] = {}
    requests_only = request_models() - {ep.response.__name__ for ep in ENDPOINTS}
    for name, model in all_models().items():
        mode = "validation" if name in requests_only else "serialization"
        files[f"schemas/{name}.json"] = dump_json(model.model_json_schema(mode=mode))
    for name, union in UNIONS.items():
        files[f"schemas/{name}.json"] = dump_json(TypeAdapter(union).json_schema(mode="serialization"))
    index = {
        "api_version": API_VERSION,
        "endpoints": [
            {
                "name": ep.name,
                "method": ep.method,
                "path": ep.path,
                "summary": ep.summary,
                "request": ep.request.__name__ if ep.request else None,
                "request_in": ep.request_in,
                "response": ep.response.__name__,
            }
            for ep in ENDPOINTS
        ],
        "stream": {
            "path": STREAM.path,
            "protocol": STREAM.protocol,
            "server_event_union": "StreamEvent",
            "client_frame_union": "ClientFrame",
            "server_events": [{"type": event_type(m), "model": m.__name__} for m in STREAM.server_events],
            "client_frames": [{"type": event_type(m), "model": m.__name__} for m in STREAM.client_frames],
        },
        "schemas": sorted(files),
    }
    files["index.json"] = dump_json(index)
    return dict(sorted(files.items()))


def export_contract(out: Path) -> list[Path]:
    """Write the contract into ``out`` (replacing stale schema files); returns written paths."""
    schemas = out / "schemas"
    if schemas.is_dir():
        shutil.rmtree(schemas)
    written: list[Path] = []
    for rel, text in build_files().items():
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        written.append(target)
    return written


def add_parser(subcommands: Any) -> None:
    contract = subcommands.add_parser("contract", help="API contract tools")
    actions = contract.add_subparsers(dest="contract_command", required=True)
    export = actions.add_parser("export", help="write JSON Schemas for every endpoint and stream event")
    export.add_argument("--out", type=Path, default=None, help="output directory (default: contract/ at the repo root)")


def run_export(args: argparse.Namespace) -> int:
    out = args.out if args.out is not None else find_repo_root() / "contract"
    written = export_contract(out)
    print(f"wrote {len(written)} files to {out}")
    return 0
