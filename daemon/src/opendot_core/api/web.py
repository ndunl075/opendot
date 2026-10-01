"""Serve the built UI and the API from one loopback origin (M3 task 3.5, ARCHITECTURE.md section 11).

- Binds to 127.0.0.1 only; every request must also *address* a loopback host (the Host header),
  which stops DNS-rebinding pages from talking to the daemon through a name they control.
- ``/v1/*`` is the API app from ``api/server.py``: bearer token on every route, the chat
  WebSocket authenticates with its subprotocol. The API never accepts cookies at all, so a
  cross-site form can never act for the user.
- A browser signs in once at ``/login`` by pasting the token (``opendot api-token show``). The page
  checks it against the API and keeps it in this origin's localStorage; the daemon never puts the
  token in a cookie (cookies are shared by every port on 127.0.0.1) or in a page it serves.
  ``index.html`` is public and holds no secrets.
- Built assets (``/assets/...``) are public: they are the same for everyone and hold no secrets.
- The desktop shell (Tauri) serves its own bundled copy of the UI and passes the token to it
  directly, so API responses allow CORS for the Tauri origins only.
- Contract endpoints the real daemon does not implement yet answer 501 ``not_implemented``, so
  the UI can show "not available yet" instead of failing on a 404.
"""

from __future__ import annotations

import asyncio
import html
import re
import secrets
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ..connections_google import CALLBACK_PATH
from .contract import ENDPOINTS
from .models import ErrorResponse

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
TAURI_ORIGINS = ("tauri://localhost", "http://tauri.localhost", "https://tauri.localhost")

TOKEN_STORAGE_KEY = "opendot.token"
"""Where the web UI keeps the access token: the browser's localStorage for exactly this origin
(scheme, host and port), which no other local port can read. Never a cookie: cookies are shared by
every port on 127.0.0.1, so any local process listening on another port would receive one
(v0.1 security review S2)."""

_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'",
    "X-Frame-Options": "DENY",
}

LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenDot: sign in</title>
<style>
:root { color-scheme: light dark; font-family: system-ui, sans-serif; }
body { display: grid; place-items: center; min-height: 100vh; margin: 0; }
form { display: grid; gap: .75rem; width: min(24rem, 90vw); }
input, button { font: inherit; padding: .6rem .75rem; border-radius: .5rem; border: 1px solid #8886; }
button { cursor: pointer; }
.error { color: #c0392b; min-height: 1.2em; }
</style></head>
<body><form id="login">
<h1>OpenDot</h1>
<label for="token">Access token</label>
<input id="token" name="token" type="password" autocomplete="off" required autofocus>
<p>Run <code>opendot api-token show</code> on this computer to see it.</p>
<p class="error" id="error" role="alert"></p>
<button type="submit">Open OpenDot</button>
</form>
<script>
document.getElementById("login").addEventListener("submit", async (event) => {
  event.preventDefault();
  const token = document.getElementById("token").value.trim();
  const error = document.getElementById("error");
  error.textContent = "";
  try {
    const response = await fetch("/v1/health", { headers: { Authorization: "Bearer " + token } });
    if (!response.ok) { error.textContent = "That token is not right."; return; }
    localStorage.setItem("opendot.token", token);
    location.replace("/");
  } catch (e) {
    error.textContent = "OpenDot is not answering. Is it running?";
  }
});
</script></body></html>
"""

LOGOUT_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>OpenDot</title></head>
<body><script>localStorage.removeItem("opendot.token"); location.replace("/login");</script></body></html>
"""


def _callback_page(message: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>OpenDot</title>'
        '<style>body{font-family:system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0}'
        "</style></head><body><p>" + html.escape(message) + "</p></body></html>"
    )


def _host_is_loopback(host_header: str | None) -> bool:
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):
        host = host.split("]", 1)[0] + "]"
    else:
        host = host.rsplit(":", 1)[0]
    return host in LOOPBACK_HOSTS


class LoopbackGuard:
    """Reject requests whose Host is not a loopback name, and add CORS for the Tauri shell only."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in scope.get("headers", [])}
        if not _host_is_loopback(headers.get("host")):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await PlainTextResponse("Forbidden host", status_code=403)(scope, receive, send)
            return
        origin = headers.get("origin")
        if scope["type"] == "http" and origin in TAURI_ORIGINS:
            if scope["method"] == "OPTIONS":
                await Response(status_code=204, headers=_cors(origin))(scope, receive, send)
                return

            async def send_with_cors(message: dict) -> None:
                if message["type"] == "http.response.start":
                    extra = [(k.lower().encode(), v.encode()) for k, v in _cors(origin).items()]
                    message = {**message, "headers": [*message.get("headers", []), *extra]}
                await send(message)

            await self.app(scope, receive, send_with_cors)
            return
        await self.app(scope, receive, send)


def _cors(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
        "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }


def _not_implemented_app(api_app: Starlette) -> ASGIApp:
    """Wrap the API so contract endpoints it lacks answer 501 instead of 404."""
    patterns = [
        (endpoint.method, re.compile("^" + re.sub(r"\{[^/]+\}", "[^/]+", endpoint.path) + "$")) for endpoint in ENDPOINTS
    ]
    known = {(route.path, method) for route in api_app.routes for method in (getattr(route, "methods", None) or [])}
    implemented = [(method, re.compile("^" + re.sub(r"\{[^/]+\}", "[^/]+", path) + "$")) for path, method in known]

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path, method = scope["path"], scope["method"]
            in_contract = any(m == method and p.match(path) for m, p in patterns)
            served = any(m == method and p.match(path) for m, p in implemented)
            if in_contract and not served:
                # Still behind the bearer check: an unauthenticated caller learns nothing.
                response: Response = JSONResponse(
                    ErrorResponse(code="not_implemented", message="Not available in this version yet.").model_dump(
                        mode="json"
                    ),
                    status_code=501,
                )
                authorized = _authorized(scope, api_app)
                if not authorized:
                    response = JSONResponse(
                        ErrorResponse(code="unauthorized", message="Missing or invalid bearer token.").model_dump(
                            mode="json"
                        ),
                        status_code=401,
                    )
                await response(scope, receive, send)
                return
        await api_app(scope, receive, send)

    return app


def _authorized(scope: Scope, api_app: Starlette) -> bool:
    token = getattr(api_app.state, "api_token", None)
    if not token:
        return False
    for key, value in scope.get("headers", []):
        if key == b"authorization":
            prefix, _, rest = value.partition(b" ")
            return prefix.lower() == b"bearer" and secrets.compare_digest(rest.strip(), token.encode())
    return False


def create_web_app(api_app: Starlette, *, token: str, ui_dist: Path | None) -> ASGIApp:
    """The daemon's single loopback app: API at /v1, login, and the built UI."""
    if not token:
        raise ValueError("an API token is required")
    api_app.state.api_token = token
    index_path = ui_dist / "index.html" if ui_dist is not None else None

    async def login(_: Request) -> Response:
        # The page checks the pasted token against the API itself and keeps it in this origin's
        # localStorage; the daemon never puts the token in a cookie or a page.
        return HTMLResponse(LOGIN_PAGE, headers=_PAGE_HEADERS)

    async def logout(_: Request) -> Response:
        return HTMLResponse(LOGOUT_PAGE, headers=_PAGE_HEADERS)

    async def spa(_: Request) -> Response:
        # Public: the built UI holds no secrets. Without a stored token it sends the user to /login.
        if index_path is None or not index_path.is_file():
            return HTMLResponse("<p>The UI is not built. Run <code>pnpm -C ui build</code>.</p>", 503)
        page = index_path.read_text(encoding="utf-8")
        return HTMLResponse(page, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    async def asset(request: Request) -> Response:
        return serve_file(f"assets/{request.path_params['path']}")

    def serve_file(relative: str) -> Response:
        if ui_dist is None:
            return PlainTextResponse("Not found", 404)
        root = ui_dist.resolve()
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file() or target.name == "index.html":
            return PlainTextResponse("Not found", 404)
        return FileResponse(target)

    async def google_callback(request: Request) -> Response:
        """Google's redirect after sign-in. No bearer token (it is a browser redirect): it is guarded
        by the one-time state and PKCE in GoogleConnector, and by the loopback Host check."""
        from ..connections_google import GoogleConnectError
        from .routes.connect import google_connector

        context = getattr(api_app.state, "context", None)
        if context is None or context.database is None:
            return HTMLResponse(_callback_page("OpenDot is not ready to connect Google."), 503)
        params = request.query_params
        try:
            await asyncio.to_thread(
                google_connector(context).complete,
                state=params.get("state"),
                code=params.get("code"),
                error=params.get("error"),
            )
        except GoogleConnectError as error:
            return HTMLResponse(_callback_page(str(error)), 400)
        except Exception:  # never echo token-endpoint details into a browser page
            return HTMLResponse(_callback_page("Google sign-in failed. Try again from OpenDot."), 502)
        return HTMLResponse(_callback_page("Google is connected. You can close this tab and go back to OpenDot."))

    routes = [
        Route(CALLBACK_PATH, google_callback, methods=["GET"]),
        Route("/login", login, methods=["GET"]),
        Route("/logout", logout, methods=["GET", "POST"]),
        Route("/assets/{path:path}", asset),
        Route("/{path:path}", _static_or_spa(ui_dist, serve_file, spa)),
    ]
    site = Starlette(routes=routes)
    api = _not_implemented_app(api_app)

    async def dispatch(scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] in ("http", "websocket") and (path == "/v1" or path.startswith("/v1/")):
            await api(scope, receive, send)
        else:
            await site(scope, receive, send)

    return LoopbackGuard(dispatch)


def _static_or_spa(ui_dist: Path | None, serve_file, spa):  # noqa: ANN001, ANN202
    """Top-level files in the build (favicon, fonts) are public; every other path is the SPA."""

    async def handle(request: Request) -> Response:
        name = request.path_params.get("path", "")
        if ui_dist is not None and name and "/" not in name and name != "index.html" and (ui_dist / name).is_file():
            return serve_file(name)
        return await spa(request)

    return handle


__all__ = ["TAURI_ORIGINS", "TOKEN_STORAGE_KEY", "create_web_app"]
