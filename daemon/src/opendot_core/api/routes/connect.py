"""Connecting apps: the user's own Google OAuth client, Google sign-in and the write opt-in, and the
GitHub token (M4 task 4.4). Listing, syncing and disconnecting live in the connections module."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...connections_google import GOOGLE_APPS, GoogleConnectError, GoogleConnector
from ...secret_store import SecretStoreError, SystemKeyringSecretStore
from ..models import (
    API_VERSION,
    Connection,
    ConnectionStart,
    ConnectionStartRequest,
    ConnectionWriteOptInRequest,
    ErrorResponse,
    GithubTokenRequest,
    GithubTokenStatus,
    GoogleClientRequest,
    GoogleClientStatus,
)
from . import ApiContext

GITHUB_TOKEN_SECRET = "github-issue-token"
GOOGLE_SETUP_STEPS = [
    "Open console.cloud.google.com and create a project (or pick one of yours).",
    "Enable the Gmail API and the Google Calendar API for it.",
    "Under APIs & Services, OAuth consent screen: choose External, add yourself as a test user.",
    "Under Credentials, Create credentials, OAuth client ID, application type Desktop app.",
    "Copy the client ID and client secret into OpenDot. They stay in this computer's keychain.",
]


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(ErrorResponse(code=code, message=message).model_dump(mode="json"), status_code=status)


async def _body(request: Request, model: Any) -> Any:
    raw = await request.body()
    return model.model_validate_json(raw if raw.strip() else b"{}")


def google_connector(ctx: ApiContext) -> GoogleConnector:
    connector = ctx.extras.get("google_connector")
    if connector is None:
        connector = GoogleConnector(ctx.database, ctx.extras.get("secret_store") or SystemKeyringSecretStore())
        ctx.extras["google_connector"] = connector
    return connector


def _connection(ctx: ApiContext, app: str) -> Connection:
    try:
        from .connections import connection_for  # the connections module builds the full view
    except ImportError:
        connection_for = None
    if connection_for is not None:
        return connection_for(ctx, app)
    connector = google_connector(ctx)
    state = connector.state()
    return Connection(
        id=app,
        app=app,  # type: ignore[arg-type]
        account_label="Google account",
        health="ok" if connector.connected() else "never_synced",  # type: ignore[arg-type]
        read_only=not connector.write_allowed(),
        write_opt_in=bool(state.get("write_opt_in")),
        write_opt_in_available=connector.client_configured(),
    )


def routes(ctx: ApiContext) -> list[Route]:
    if ctx.database is None:
        return []
    v = f"/{API_VERSION}"

    def secrets():  # resolved per request: the runtime injects its store after these routes are built
        return ctx.extras.get("secret_store") or SystemKeyringSecretStore()

    async def google_client_get(_: Request) -> Response:
        configured = await asyncio.to_thread(google_connector(ctx).client_configured)
        return JSONResponse(GoogleClientStatus(configured=configured, setup_steps=GOOGLE_SETUP_STEPS).model_dump(mode="json"))

    async def google_client_set(request: Request) -> Response:
        try:
            body: GoogleClientRequest = await _body(request, GoogleClientRequest)
            await asyncio.to_thread(google_connector(ctx).save_client, body.client_id, body.client_secret)
        except ValidationError as error:
            return _error(422, "invalid_request", str(error.errors()[0]["msg"]))
        except GoogleConnectError as error:
            return _error(422, "invalid_google_client", str(error))
        return JSONResponse(GoogleClientStatus(configured=True, setup_steps=GOOGLE_SETUP_STEPS).model_dump(mode="json"))

    async def github_token_set(request: Request) -> Response:
        try:
            body: GithubTokenRequest = await _body(request, GithubTokenRequest)
        except ValidationError as error:
            return _error(422, "invalid_request", str(error.errors()[0]["msg"]))
        token = body.token.strip()
        if not token.startswith(("github_pat_", "ghp_")) or any(ch.isspace() for ch in token):
            return _error(422, "invalid_github_token", "That does not look like a GitHub personal access token.")
        await asyncio.to_thread(secrets().store, GITHUB_TOKEN_SECRET, token)
        return JSONResponse(GithubTokenStatus(configured=True).model_dump(mode="json"))

    async def github_token_remove(_: Request) -> Response:
        try:
            await asyncio.to_thread(secrets().delete, GITHUB_TOKEN_SECRET)
        except (SecretStoreError, AttributeError):
            pass
        return JSONResponse(GithubTokenStatus(configured=False).model_dump(mode="json"))

    async def connection_start(request: Request) -> Response:
        try:
            body: ConnectionStartRequest = await _body(request, ConnectionStartRequest)
        except ValidationError as error:
            return _error(422, "invalid_request", str(error.errors()[0]["msg"]))
        if body.app == "github":
            return _error(400, "use_github_token", "GitHub connects with a personal access token: save one instead.")
        try:
            url, state = await asyncio.to_thread(google_connector(ctx).start)
        except GoogleConnectError as error:
            return _error(409, "google_client_missing", str(error))
        return JSONResponse(ConnectionStart(app=body.app, authorize_url=url, state=state).model_dump(mode="json"))

    async def connection_write_opt_in(request: Request) -> Response:
        app = request.path_params["connection_id"]
        if app not in GOOGLE_APPS:
            return _error(404, "not_found", "Only Gmail and Google Calendar have a write opt-in.")
        try:
            body: ConnectionWriteOptInRequest = await _body(request, ConnectionWriteOptInRequest)
            _, url = await asyncio.to_thread(google_connector(ctx).set_write_opt_in, body.enabled)
        except ValidationError as error:
            return _error(422, "invalid_request", str(error.errors()[0]["msg"]))
        except GoogleConnectError as error:
            return _error(409, "google_client_missing", str(error))
        connection = (await asyncio.to_thread(_connection, ctx, app)).model_copy(update={"authorize_url": url})
        return JSONResponse(connection.model_dump(mode="json"))

    return [
        Route(f"{v}/connections/google/client", google_client_get, methods=["GET"]),
        Route(f"{v}/connections/google/client", google_client_set, methods=["PUT"]),
        Route(f"{v}/connections/github/token", github_token_set, methods=["PUT"]),
        Route(f"{v}/connections/github/token", github_token_remove, methods=["DELETE"]),
        Route(f"{v}/connections/start", connection_start, methods=["POST"]),
        Route(f"{v}/connections/{{connection_id}}/write-opt-in", connection_write_opt_in, methods=["PUT"]),
    ]


__all__ = ["GITHUB_TOKEN_SECRET", "google_connector", "routes"]
