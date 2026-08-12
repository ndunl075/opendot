"""Shared loopback bearer-token auth for Alfred's local HTTP surfaces.

Used by the Streamable HTTP MCP transport (``mcp_server.run_streamable_http``)
and the admin UI (``admin_ui.py``) so "authenticate every request, outside
the wrapped app's own handling" is implemented once, not twice -- per
CONTRIBUTING.md's own rule: if the same check would otherwise be written in
two transports, it belongs in a shared module instead.
"""

from __future__ import annotations

import secrets
from typing import Any

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import Receive, Scope, Send


def generate_token() -> str:
    """Return a fresh random bearer token."""
    return secrets.token_urlsafe(32)


def bearer_token(header_value: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header value."""
    if not header_value or not header_value.lower().startswith("bearer "):
        return None
    token = header_value[len("bearer ") :].strip()
    return token or None


class BearerAuthMiddleware:
    """Reject every HTTP request that lacks the exact configured bearer token.

    Applied outside the wrapped ASGI app, so a rejection never reaches its
    own routing/session machinery -- an unauthenticated caller cannot open
    an MCP session, load an admin UI page, or see anything about either.
    """

    def __init__(self, app: Any, *, expected_token: str) -> None:
        self._app = app
        self._expected_token = expected_token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        supplied = bearer_token(Headers(scope=scope).get("authorization"))
        if supplied is None or not secrets.compare_digest(supplied, self._expected_token):
            await PlainTextResponse("Unauthorized", status_code=401)(scope, receive, send)
            return
        await self._app(scope, receive, send)
