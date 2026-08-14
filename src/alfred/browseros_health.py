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

import socket

from .connector_health import ConnectorHealth

BROWSEROS_HOST = "127.0.0.1"
BROWSEROS_PORT = 9200


def browseros_health(
    *,
    host: str = BROWSEROS_HOST,
    port: int = BROWSEROS_PORT,
    timeout: float = 0.3,
) -> ConnectorHealth:
    """Probe BrowserOS neo's local MCP port and report it in connector-health shape.

    Reuses ``ConnectorHealth`` so the connectors page template needs no
    special case: this either renders alongside every sync_state-derived
    row unchanged. ``last_success_at`` is always None -- "reachable right
    now" isn't a sync event with a timestamp worth persisting, and a live
    probe has nothing historical to report anyway.
    """
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
