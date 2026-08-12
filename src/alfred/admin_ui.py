"""A quiet, editorial, read-only admin dashboard for a running Alfred instance.

Section 10 slice 7 named an admin UI as unbuilt, with no shape decided --
unlike every other item finished this session, there was no documented
target to build against, so the shape below is a deliberate design choice:
a single-owner workspace view (sidebar navigation, one page per concern),
not a status page. Kept strictly read-only, matching every connector's own
"start read-only" precedent (decision 8): approving a pending action still
goes through the CLI (``alfred approval-approve``), never this page. No
external network calls -- no CDN fonts, no CDN icons -- consistent with
Alfred's local-first rule that nothing calls out by default.

Loopback-only by default, like every other local HTTP surface in this
codebase, but ``run_admin_ui``'s ``host`` is a real parameter, not a hard
invariant -- this one is meant for a human to look at, sometimes from a
phone over a VPN, and ``127.0.0.1`` alone is unreachable from anywhere but
the machine itself no matter what network route got there. See
``run_admin_ui``'s docstring before binding it to anything else.

Auth is delivered differently from the MCP transports too: an MCP client
can set an Authorization header on every call; a browser navigating
between pages cannot. So this accepts either an Authorization header (for
`curl`/scripting) or a same-origin session cookie set by a small login
page -- the cookie's value *is* the bearer token (no separate session
store), which is only as sensitive as the token itself and travels no
further than wherever the operator chose to bind this.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.staticfiles import StaticFiles

from .audit import AuditLog
from .briefing import BriefingService
from .connector_health import connector_health
from .db import Database
from .http_auth import bearer_token
from .policy import ApprovalService

_SESSION_COOKIE = "alfred_admin_token"
_TEMPLATES_DIR = Path(__file__).parent / "templates" / "admin"
_STATIC_DIR = Path(__file__).parent / "static" / "admin"

_env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=select_autoescape())


def _render(template_name: str, **context: Any) -> HTMLResponse:
    return HTMLResponse(_env.get_template(template_name).render(**context))


def _format_dt(value: datetime | None) -> str | None:
    """Render a timestamp the way a human reads one, not raw ISO-with-microseconds.

    Deliberately avoids platform-specific strftime flags (no ``%-d``/``%#d``)
    since this needs to run identically on every OS Alfred supports, not
    just the one it happened to be developed on.
    """
    if value is None:
        return None
    local = value.astimezone()
    hour_12 = local.hour % 12 or 12
    return f"{local:%b} {local.day}, {local.year} · {hour_12}:{local:%M %p}"


def _result_preview(result: dict[str, Any], *, max_length: int = 80) -> str:
    text = ", ".join(f"{key}={value}" for key, value in sorted(result.items()))
    return text if len(text) <= max_length else text[: max_length - 1] + "…"


_env.filters["dt"] = _format_dt


class _SessionAuthMiddleware(BaseHTTPMiddleware):
    """Redirect anything unauthenticated to /login instead of a bare 401.

    /login and /static are always reachable -- otherwise a visitor could
    never get far enough to sign in, or the login page's own stylesheet
    would 401.
    """

    def __init__(self, app: Any, *, expected_token: str) -> None:
        super().__init__(app)
        self._expected_token = expected_token

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.url.path == "/login" or request.url.path.startswith("/static/"):
            return await call_next(request)
        supplied = bearer_token(request.headers.get("authorization")) or request.cookies.get(_SESSION_COOKIE)
        if supplied is None or not secrets.compare_digest(supplied, self._expected_token):
            return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)
        return await call_next(request)


def create_admin_app(database: Database, *, bearer_token_value: str) -> Starlette:
    """Build the admin UI, gated by ``bearer_token_value`` on every route but /login."""

    async def login(request: Request) -> Response:
        next_path = request.query_params.get("next", "/")
        if not next_path.startswith("/"):
            next_path = "/"
        if request.method == "POST":
            form = await request.form()
            supplied = str(form.get("token", ""))
            if supplied and secrets.compare_digest(supplied, bearer_token_value):
                response = RedirectResponse(url=str(form.get("next") or "/"), status_code=303)
                response.set_cookie(
                    _SESSION_COOKIE, supplied, httponly=True, samesite="strict", max_age=60 * 60 * 24 * 30
                )
                return response
            return _render("login.html", error="Incorrect token.", next=next_path)
        return _render("login.html", error=None, next=next_path)

    async def overview(request: Request) -> Response:
        brief = BriefingService(database).morning_brief()
        sections = [
            {"label": "Overdue", "entries": brief.overdue, "overdue": True},
            {"label": "Due today", "entries": brief.due_today, "overdue": False},
            {"label": "Next 7 days", "entries": brief.upcoming, "overdue": False},
            {"label": "Canvas missing", "entries": brief.missing_assignments, "overdue": False},
            {"label": "Today's calendar", "entries": brief.calendar_today, "overdue": False},
            {"label": "GitHub notifications", "entries": brief.github_notifications, "overdue": False},
            {"label": "No due date", "entries": brief.no_due_date, "overdue": False},
        ]
        return _render(
            "overview.html",
            active="overview",
            generated_at=brief.generated_at,
            sections=sections,
            has_any_items=any(section["entries"] for section in sections),
        )

    async def approvals_page(request: Request) -> Response:
        approvals = ApprovalService(database).list_pending()
        return _render("approvals.html", active="approvals", approvals=approvals)

    async def connectors_page(request: Request) -> Response:
        return _render("connectors.html", active="connectors", connectors=connector_health(database))

    async def audit_page(request: Request) -> Response:
        records = AuditLog(database).recent(limit=50)
        return _render(
            "audit.html",
            active="audit",
            records=[{"record": record, "result_preview": _result_preview(record.result)} for record in records],
        )

    app = Starlette(
        routes=[
            Route("/login", login, methods=["GET", "POST"]),
            Route("/", overview),
            Route("/approvals", approvals_page),
            Route("/connectors", connectors_page),
            Route("/audit", audit_page),
        ],
        middleware=[],
    )
    app.add_middleware(_SessionAuthMiddleware, expected_token=bearer_token_value)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    return app


def run_admin_ui(database: Database, *, port: int, bearer_token_value: str, host: str = "127.0.0.1") -> None:
    """Serve the admin UI. Defaults to loopback-only, matching every other
    local HTTP surface in this codebase -- but unlike the MCP transports,
    this one is meant for a human to look at, sometimes from another
    device, so ``host`` is a real parameter here rather than a hard
    invariant. Binding to ``127.0.0.1`` alone is *not* reachable through a
    VPN/Tailscale connection to this machine -- loopback only ever accepts
    connections from the machine itself, regardless of what network route
    got there. To view this from a phone, bind to this host's actual VPN
    interface address (e.g. its Tailscale IP) instead, never ``0.0.0.0``
    unless you have your own firewall rules already restricting who can
    reach this port.

    Takes an already-resolved ``Database`` rather than a path, since the
    CLI caller has already resolved one via ``database_from_args``.
    """
    import uvicorn

    app = create_admin_app(database, bearer_token_value=bearer_token_value)
    uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning")).run()
