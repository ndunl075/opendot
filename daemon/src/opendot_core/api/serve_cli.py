"""`opendot serve` and `opendot api-token`: the daemon's UI and API on 127.0.0.1 (M3 task 3.5).

The access token lives in the OS keychain (never SQLite, logs or git). `opendot serve` creates it
on first run. On a headless server without a keychain, `--token-file` reads it from a file the
user protects (see `deploy/`); `opendot doctor` warns about that (M4).
"""

from __future__ import annotations

import argparse
import json
import os
import stat
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
    opener = subparsers.add_parser("open", help="open OpenDot in your browser (signs in with a one-time link)")
    opener.add_argument("--port", type=int, default=DEFAULT_PORT)
    opener.add_argument("--no-browser", action="store_true", help="print the one-time link instead of opening it")
    opener.add_argument("--token-file", help="read the access token from this file instead of the OS keychain")
    token = subparsers.add_parser("api-token", help="show or rotate the UI/API access token")
    token.add_argument("action", choices=["show", "rotate"])


class InsecureTokenFile(SystemExit):
    """The token file could be read by someone other than its owner."""


def read_token_file(path: Path) -> str:
    """Read a headless token file, refusing one other local users could read or swap (POSIX).

    The file is opened once (never following a symlink) and the owner and mode are checked on that
    same open file, so nothing can replace it between the check and the read (review S5). On Windows
    the file inherits the user profile's ACLs; doctor warns about headless secrets files."""
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise InsecureTokenFile(
                    f"{path} must be a regular file owned by you and readable only by you (chmod 600 {path}); "
                    "refusing to use it."
                )
            with os.fdopen(os.dup(fd), "r", encoding="utf-8") as handle:
                token = handle.read().strip()
        finally:
            os.close(fd)
    else:
        token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise InsecureTokenFile(f"{path} does not hold a full access token (run `opendot api-token rotate`).")
    return token


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


def run_open(args: argparse.Namespace, *, store: SecretStore | None = None, out: Any = None, client: Any = None) -> int:
    """Sign the browser in without ever pasting the access token into a page (review S10)."""
    import webbrowser

    from .session_client import NotOurDaemon, exchange

    out = out or sys.stdout
    if getattr(args, "token_file", None):
        token = read_token_file(Path(args.token_file))
    else:
        token = load_or_create_token(store or SystemKeyringSecretStore())
    base = f"http://{HOST}:{args.port}"
    try:
        code = exchange(base, token, kind="login_code", client=client)
    except NotOurDaemon as error:
        out.write(f"Refusing to sign in: {error}.\n")
        return 2
    except Exception as error:  # daemon not running, network error
        out.write(f"OpenDot is not answering at {base} ({type(error).__name__}). Start it with `opendot serve`.\n")
        return 1
    url = f"{base}/login#code={code}"
    if args.no_browser:
        out.write(f"Open this link within two minutes (it works once): {url}\n")
    else:
        webbrowser.open(url)
        out.write("Opened OpenDot in your browser.\n")
    return 0


def run_token(args: argparse.Namespace, *, store: SecretStore | None = None, out: Any = None) -> int:
    out = out or sys.stdout
    store = store or SystemKeyringSecretStore()
    if args.action == "rotate":
        store.store(TOKEN_SECRET, generate_token())
        out.write("Rotated. Sign in again in every open OpenDot window.\n")
        return 0
    out.write(load_or_create_token(store) + "\n")
    return 0


def build_serve_app(  # noqa: ANN201
    database: Database,
    *,
    token: str,
    ui_dist: Path | None,
    registry: Any = None,
    tools: Any = None,
    port: int = DEFAULT_PORT,
    secret_store: Any = None,
):
    """The whole loopback app. ``registry`` defaults to the ChatGPT plan provider only."""
    from ..agent.runtime import build_agent_runtime
    from ..providers.registry import ProviderRegistry, ProviderSettings
    from .web import create_web_app

    if registry is None:
        from ..providers.chatgpt_plan import ChatGPTPlanProvider

        registry = ProviderRegistry(ProviderSettings())
        registry.register(ChatGPTPlanProvider())
    if tools is None:
        from ..agent.mcp_tools import McpTools

        tools = McpTools(database.path)
    secrets = secret_store or SystemKeyringSecretStore()
    runtime = build_agent_runtime(
        database, registry, tools, api_token=token, actor=getattr(tools, "actor", "owner"), secret_store=secrets
    )
    from ..connections_google import GoogleConnector
    from ..connector_sync import GITHUB_TOKEN_SECRET, build_syncers

    extras = runtime.app.state.context.extras
    extras["secret_store"] = secrets
    extras["connector_syncers"] = build_syncers(database, secrets)

    def disconnect_gmail() -> None:
        extras["google_connector"].disconnect("gmail")

    def disconnect_calendar() -> None:
        extras["google_connector"].disconnect("google_calendar")

    def disconnect_github() -> None:
        try:
            secrets.delete(GITHUB_TOKEN_SECRET)
        except (SecretStoreError, AttributeError):
            pass

    extras["connector_disconnectors"] = {
        "gmail": disconnect_gmail,
        "google_calendar": disconnect_calendar,
        "github": disconnect_github,
    }
    runtime.app.state.context.extras["google_connector"] = GoogleConnector(
        database, secrets, base_url=f"http://{HOST}:{port}"
    )
    runtime.loop.resume_all()
    return create_web_app(runtime.app, token=token, ui_dist=ui_dist), runtime


class NoTools:
    """A tool executor with no tools (tests, or a daemon started with tools turned off)."""

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
        token = read_token_file(Path(args.token_file))
    else:
        token = load_or_create_token(SystemKeyringSecretStore())
    ui_dist = Path(args.ui_dist) if args.ui_dist else default_ui_dist()
    app, _ = build_serve_app(database, token=token, ui_dist=ui_dist, port=args.port)
    config = uvicorn.Config(app, host=HOST, port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    if args.print_ready:
        original_startup = server.startup

        async def startup(sockets: Any = None) -> None:
            await original_startup(sockets)
            print(json.dumps({"ready": True, "url": f"http://{HOST}:{args.port}"}), flush=True)

        server.startup = startup  # type: ignore[method-assign]
    else:
        print(f"OpenDot is running at http://{HOST}:{args.port} (run `opendot open` to open it in your browser)")
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
