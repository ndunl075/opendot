"""`opendot serve` and `opendot api-token`: the daemon's UI and API on 127.0.0.1 (M3 task 3.5).

The access token lives in the OS keychain (never SQLite, logs or git). `opendot serve` creates it
on first run. On a headless server without a keychain, `--token-file` reads it from a file the
user protects (see `deploy/`); `opendot doctor` warns about that (M4).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ..db import Database
from ..http_auth import generate_token
from ..secret_store import SecretStore, SecretStoreError, SystemKeyringSecretStore

TOKEN_SECRET = "opendot-api-token"
DEFAULT_PORT = 8765
HOST = "127.0.0.1"


def register(subparsers: Any) -> None:
    serve = subparsers.add_parser("serve", help="serve the OpenDot UI and API on 127.0.0.1")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--ui-dist", help="built UI directory (default: ui/dist next to the daemon, or OPENDOT_UI_DIST)")
    serve.add_argument("--token-file", help="read the access token from this file instead of the OS keychain")
    serve.add_argument("--log-file", help="append stdout and stderr to this file (used by the Windows service)")
    serve.add_argument(
        "--no-background",
        action="store_true",
        help="serve only the API and UI; skip the always-on loop (due jobs, reminders, syncs, keep-awake)",
    )
    serve.add_argument("--print-ready", action="store_true", help="print one JSON line when listening (for the desktop shell)")
    token = subparsers.add_parser("api-token", help="show or rotate the UI/API access token")
    token.add_argument("action", choices=["show", "rotate"])


def load_or_create_token(store: SecretStore) -> str:
    try:
        return store.get_required(TOKEN_SECRET)
    except SecretStoreError:
        token = generate_token()
        store.store(TOKEN_SECRET, token)
        return token


def default_ui_dist() -> Path | None:
    env = os.environ.get("OPENDOT_UI_DIST")
    if env:
        return Path(env)
    bundled = getattr(sys, "_MEIPASS", None)  # PyInstaller sidecar: the UI ships inside it
    if bundled:
        candidate = Path(bundled) / "ui"
        if candidate.is_dir():
            return candidate
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "ui" / "dist"
        if candidate.is_dir():
            return candidate
    return None


def run_token(args: argparse.Namespace, *, store: SecretStore | None = None, out: Any = None) -> int:
    out = out or sys.stdout
    store = store or SystemKeyringSecretStore()
    if args.action == "rotate":
        store.store(TOKEN_SECRET, generate_token())
        out.write("Rotated. Sign in again in every open OpenDot window.\n")
        return 0
    out.write(load_or_create_token(store) + "\n")
    return 0


def build_serve_app(database: Database, *, token: str, ui_dist: Path | None, registry: Any = None, tools: Any = None):  # noqa: ANN201
    """The whole loopback app. ``registry`` defaults to the ChatGPT plan provider only."""
    from ..agent.runtime import build_agent_runtime
    from ..providers.registry import ProviderRegistry, ProviderSettings
    from .web import create_web_app

    if registry is None:
        from ..providers.chatgpt_plan import ChatGPTPlanProvider

        registry = ProviderRegistry(ProviderSettings())
        registry.register(ChatGPTPlanProvider())
    runtime = build_agent_runtime(database, registry, tools if tools is not None else NoTools(), api_token=token)
    runtime.loop.resume_all()
    return create_web_app(runtime.app, token=token, ui_dist=ui_dist), runtime


class NoTools:
    """Until the connector tools are wired in (M4 task 4.4), chat runs with no tools."""

    actor = "owner"

    def specs(self) -> list:
        return []

    def intent(self, name: str, arguments: Any) -> Any:
        raise KeyError(name)

    def run(self, name: str, arguments: Any, *, task_id: str, call_id: str) -> str:
        raise KeyError(name)

    def propose(self, name: str, arguments: Any, *, task_id: str) -> Any:
        raise KeyError(name)


def redirect_output(path: str) -> None:
    """Send this process's stdout and stderr to ``path`` (line-buffered, appended)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    stream = open(target, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives as long as the process
    sys.stdout = stream
    sys.stderr = stream


def run_serve(args: argparse.Namespace, database: Database, *, worker: Any = None) -> int:
    import uvicorn

    if getattr(args, "log_file", None):
        redirect_output(args.log_file)

    if args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    else:
        token = load_or_create_token(SystemKeyringSecretStore())
    ui_dist = Path(args.ui_dist) if args.ui_dist else default_ui_dist()
    app, _ = build_serve_app(database, token=token, ui_dist=ui_dist)
    config = uvicorn.Config(app, host=HOST, port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    if args.print_ready:
        original_startup = server.startup

        async def startup(sockets: Any = None) -> None:
            await original_startup(sockets)
            print(json.dumps({"ready": True, "url": f"http://{HOST}:{args.port}"}), flush=True)

        server.startup = startup  # type: ignore[method-assign]
    else:
        print(f"OpenDot is running at http://{HOST}:{args.port} (sign in with `opendot api-token show`)")
    if worker is None and not getattr(args, "no_background", False):
        from ..always_on import AlwaysOnWorker

        worker = AlwaysOnWorker(database)
    if worker is not None:
        worker.start()
    try:
        server.run()
    finally:
        if worker is not None:
            worker.stop()
    return 0


__all__ = ["DEFAULT_PORT", "TOKEN_SECRET", "build_serve_app", "load_or_create_token", "register", "run_serve", "run_token"]
