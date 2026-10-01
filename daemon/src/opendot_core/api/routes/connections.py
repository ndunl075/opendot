"""Connections: list, sync now and disconnect (the connect flow and write opt-in live elsewhere).

A connection's id is its app name (``gmail``, ``google_calendar``, ``github``): v0.1 has one account
per app. Health comes from ``connector_health`` (ok / stale / error / never synced). Wiring that
needs credentials is injected through ``ApiContext.extras``:

- ``connector_syncers``: ``{app: callable()}`` that runs one sync of that app.
- ``connector_disconnectors``: ``{app: callable()}`` that revokes and deletes that app's credentials.
- ``secret_store``: where to look for the Google OAuth client id (defaults to the OS keychain).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, get_args

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ...audit import AuditEvent, AuditLog
from ...connector_health import connector_health
from ...secret_store import SystemKeyringSecretStore
from ..models import (
    API_VERSION,
    Connection,
    ConnectionApp,
    ConnectionDisconnectRequest,
    ConnectionDisconnectResult,
    ConnectionList,
)
from ._common import error, invalid, read_body, reply, settings_for, utcnow
from .memory import forget_source, memory_count

if TYPE_CHECKING:
    from . import ApiContext

APPS: tuple[str, ...] = get_args(ConnectionApp)
GOOGLE_APPS = ("gmail", "google_calendar")
_LABELS = {"gmail": "Gmail", "google_calendar": "Google Calendar", "github": "GitHub"}
_SCOPE_WORD = {"gmail": "gmail", "google_calendar": "calendar"}
_PLACEHOLDER_ACCOUNTS = {"", "self", "primary", "default"}
GOOGLE_CLIENT_SECRET = "google-oauth-client-id"


def _google_state(ctx: Any) -> dict[str, Any]:
    settings = settings_for(ctx)
    return settings.get("google_connection", dict, {}) if settings is not None else {}


def _google_available(ctx: Any) -> bool:
    store = ctx.extras.get("secret_store") or SystemKeyringSecretStore()
    try:
        return bool(store.get_optional(GOOGLE_CLIENT_SECRET))
    except Exception:  # no keychain on this machine: the Google flow cannot be offered
        return False


def _granted_apps(ctx: Any) -> set[str]:
    scopes = [str(s).lower() for s in _google_state(ctx).get("granted_scopes") or []]
    return {app for app, word in _SCOPE_WORD.items() if any(word in scope for scope in scopes)}


def connection_for(ctx: Any, app: str) -> Connection:
    """The Connection for one app, whether or not it has ever synced."""
    health = next((h for h in connector_health(ctx.database) if h.connector == app), None)
    google = app in GOOGLE_APPS
    state = _google_state(ctx) if google else {}
    opted_in = bool(state.get("write_opt_in")) if google else False
    label = _LABELS[app]
    account = health.account if health is not None else ""
    if health is None:
        detail = "Connected, but it has not synced yet."
    elif health.last_error:
        detail = f"The last sync failed ({health.last_error})."
    elif health.state == "stale":
        detail = "It has not synced for over a day."
    else:
        detail = None
    return Connection(
        id=app,
        app=app,  # type: ignore[arg-type]
        account_label=account if account not in _PLACEHOLDER_ACCOUNTS else label,
        health=health.state if health is not None else "never_synced",
        last_synced_at=health.last_success_at if health is not None else None,
        health_detail=detail,
        read_only=not opted_in,
        write_opt_in=opted_in,
        write_opt_in_available=google and _google_available(ctx),
        memory_item_count=memory_count(ctx.database, app),
    )


def connected_apps(ctx: Any) -> list[str]:
    """Apps that have a sync record, or that Google sign-in has granted but never synced."""
    have = {h.connector for h in connector_health(ctx.database)} | _granted_apps(ctx)
    return [app for app in APPS if app in have]


def _record_sync_failure(ctx: Any, app: str, problem: Exception) -> None:
    now = utcnow().isoformat()
    with ctx.database.connect() as connection:
        with ctx.database.transaction(connection):
            connection.execute(
                "INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at) "
                "VALUES (?, 'self', NULL, NULL, ?, ?) ON CONFLICT(connector, account) DO UPDATE SET "
                "last_error = excluded.last_error, updated_at = excluded.updated_at",
                (app, type(problem).__name__, now),
            )


def _sync(ctx: Any, app: str, syncer: Any) -> Connection:
    try:
        syncer()
    except Exception as problem:  # the connector records its own error; make sure one is visible
        if not any(h.connector == app and h.last_error for h in connector_health(ctx.database)):
            _record_sync_failure(ctx, app, problem)
    return connection_for(ctx, app)


def _disconnect(ctx: Any, app: str, forget_learned: bool) -> int:
    disconnector = (ctx.extras.get("connector_disconnectors") or {}).get(app)
    if callable(disconnector):
        disconnector()
    forgotten = forget_source(ctx.database, app, actor="user:ui") if forget_learned else 0
    with ctx.database.connect() as connection:
        with ctx.database.transaction(connection):
            connection.execute("DELETE FROM sync_state WHERE connector = ?", (app,))
            connection.execute("DELETE FROM connector_records WHERE connector = ?", (app,))
            AuditLog.append_in_transaction(
                connection,
                AuditEvent(
                    actor=ctx.actor, client="api", tool="connection_disconnect", outcome="ok",
                    result={"app": app, "forget_learned": forget_learned, "forgotten_count": forgotten},
                ),
            )
    settings = settings_for(ctx)
    if settings is not None and app in _SCOPE_WORD:
        state = settings.get("google_connection", dict, {})
        if state.get("granted_scopes"):
            remaining = [s for s in state["granted_scopes"] if _SCOPE_WORD[app] not in str(s).lower()]
            state = {**state, "granted_scopes": remaining}
            if not remaining:
                state["write_opt_in"] = False
            settings.set("google_connection", state)
    return forgotten


def routes(ctx: ApiContext) -> list[Route]:
    if ctx.database is None:
        return []

    def known(app: str) -> bool:
        return app in connected_apps(ctx)

    async def list_connections(_: Request) -> Response:
        def build() -> ConnectionList:
            return ConnectionList(connections=[connection_for(ctx, app) for app in connected_apps(ctx)])

        return reply(await asyncio.to_thread(build))

    async def sync(request: Request) -> Response:
        app = request.path_params["connection_id"]
        if not await asyncio.to_thread(known, app):
            return error(404, "not_found", "No such connection.")
        syncer = (ctx.extras.get("connector_syncers") or {}).get(app)
        if not callable(syncer):
            return error(409, "sync_unavailable", "This app cannot be synced from here yet.")
        return reply(await asyncio.to_thread(_sync, ctx, app, syncer))

    async def disconnect(request: Request) -> Response:
        app = request.path_params["connection_id"]
        try:
            body: ConnectionDisconnectRequest = await read_body(request, ConnectionDisconnectRequest)
        except ValidationError as problem:
            return invalid(problem)
        if not await asyncio.to_thread(known, app):
            return error(404, "not_found", "No such connection.")
        forgotten = await asyncio.to_thread(_disconnect, ctx, app, body.forget_learned)
        return reply(ConnectionDisconnectResult(disconnected=True, forgotten_count=forgotten))

    v = f"/{API_VERSION}/connections"
    return [
        Route(v, list_connections, methods=["GET"]),
        Route(f"{v}/{{connection_id}}/sync", sync, methods=["POST"]),
        Route(f"{v}/{{connection_id}}/disconnect", disconnect, methods=["POST"]),
    ]
