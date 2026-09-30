"""Live reachability check for BrowserOS neo's local MCP endpoint.

BrowserOS neo (see hermes-profile/README.md point 8) is not a connector in
the sense every other entry on the admin UI's connectors page is: it never
writes to Alfred's own ``sync_state`` table (or anything else in Alfred's
database) because Hermes talks to it directly over MCP, with zero
involvement from Alfred Core. There is no synced-record history to classify
the way ``connector_health.py`` does.

So this isn't a health *classification* over stored rows -- it's a live
probe of a loopback TCP port, done at page-render time. That's still
consistent with admin_ui's "no external network calls" rule: 127.0.0.1 is
local, not external, and a plain TCP connect (no HTTP request, no payload)
is the least a health check can possibly do. A short timeout keeps a closed
port from making the connectors page noticeably slow to load.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

from .connector_health import ConnectorHealth

BROWSEROS_HOST = "127.0.0.1"

#: The port BrowserOS's own documentation gives. Used only when its config
#: cannot be read: the installed build actually chose 9210, so treating the
#: documented value as authoritative would report "not running" while the
#: server was answering perfectly well one port over.
BROWSEROS_DEFAULT_PORT = 9200

#: BrowserOS records its live ports here, under `ports.server`. Reading them
#: beats hardcoding, since the value is a real setting the app persists rather
#: than a constant, and a mismatch fails in the most misleading possible way --
#: a healthy service reported as down.
_CONFIG_PATH = Path("AppData/Local/BrowserClaw/User Data/.browseros/config.json")


def browseros_port(*, default: int = BROWSEROS_DEFAULT_PORT) -> int:
    """Return the MCP port BrowserOS is configured to serve on.

    Falls back to the documented default whenever the config is missing,
    unreadable, or does not name a port -- BrowserOS not being installed is
    the ordinary case, not an error worth raising from a health probe.
    """
    home = os.environ.get("LOCALAPPDATA")
    candidates = [Path(home) / "BrowserClaw/User Data/.browseros/config.json"] if home else []
    candidates.append(Path.home() / _CONFIG_PATH)
    for candidate in candidates:
        try:
            ports = json.loads(candidate.read_text(encoding="utf-8")).get("ports")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(ports, dict) and isinstance(ports.get("server"), int):
            return int(ports["server"])
    return default


def browseros_health(
    *,
    host: str = BROWSEROS_HOST,
    port: int | None = None,
    timeout: float = 0.3,
) -> ConnectorHealth:
    """Probe BrowserOS neo's local MCP port and report it in connector-health shape.

    Reuses ``ConnectorHealth`` so the connectors page template needs no
    special case: this either renders alongside every sync_state-derived
    row unchanged. ``last_success_at`` is always None -- "reachable right
    now" isn't a sync event with a timestamp worth persisting, and a live
    probe has nothing historical to report anyway.

    ``port`` defaults to whatever BrowserOS's own config says it is served
    on, not to a constant, so an install that picked a different port is
    still reported accurately.
    """
    if port is None:
        port = browseros_port()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return ConnectorHealth(
                connector="browseros",
                account=f"{host}:{port}",
                state="ok",
                last_success_at=None,
                last_error=None,
            )
    except OSError as error:
        return ConnectorHealth(
            connector="browseros",
            account=f"{host}:{port}",
            state="error",
            last_success_at=None,
            last_error=f"BrowserOS neo not reachable ({error.__class__.__name__}) -- is the app running?",
        )
