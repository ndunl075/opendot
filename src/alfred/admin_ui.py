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
from markupsafe import Markup
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.staticfiles import StaticFiles

from .audit import AuditLog
from .briefing import BriefingService
from .browseros_health import browseros_health
from .connector_health import connector_health
from .db import Database
from .evaluation import EvaluationService
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


# Inline SVG path data only -- no <img src> to a CDN, matching this module's
# own "no external network calls, no CDN icons" rule (see module docstring).
# Path data is real brand mark data (simple-icons project, CC0), not a
# hand-approximated shape, so it actually reads as the connector's real logo.
# google_health has no real Google Health/Fit mark available from that
# source, so it gets an honest generic glyph instead of a fabricated one.
_CONNECTOR_ICON_PATHS: dict[str, str] = {
    "gmail": (
        'M24 5.457v13.909c0 .904-.732 1.636-1.636 1.636h-3.819V11.73L12 16.64l-6.545-4.91v9.273H1.636A1.636 1.636 0 0 1 0 19.366V5.457c0-2.023 2.309-3.178 3.927-1.964L5.455 4.64 12 9.548l6.545-4.91 1.528-1.145C21.69 2.28 24 3.434 24 5.457z'
    ),
    # same service as gmail -- inbound commands read the same mailbox
    "gmail_inbound": (
        'M24 5.457v13.909c0 .904-.732 1.636-1.636 1.636h-3.819V11.73L12 16.64l-6.545-4.91v9.273H1.636A1.636 1.636 0 0 1 0 19.366V5.457c0-2.023 2.309-3.178 3.927-1.964L5.455 4.64 12 9.548l6.545-4.91 1.528-1.145C21.69 2.28 24 3.434 24 5.457z'
    ),
    "google_calendar": (
        'M18.316 5.684H24v12.632h-5.684V5.684zM5.684 24h12.632v-5.684H5.684V24zM18.316 5.684V0H1.895A1.894 1.894 0 0 0 0 1.895v16.421h5.684V5.684h12.632zm-7.207 6.25v-.065c.272-.144.5-.349.687-.617s.279-.595.279-.982c0-.379-.099-.72-.3-1.025a2.05 2.05 0 0 0-.832-.714 2.703 2.703 0 0 0-1.197-.257c-.6 0-1.094.156-1.481.467-.386.311-.65.671-.793 1.078l1.085.452c.086-.249.224-.461.413-.633.189-.172.445-.257.767-.257.33 0 .602.088.816.264a.86.86 0 0 1 .322.703c0 .33-.12.589-.36.778-.24.19-.535.284-.886.284h-.567v1.085h.633c.407 0 .748.109 1.02.327.272.218.407.499.407.843 0 .336-.129.614-.387.832s-.565.327-.924.327c-.351 0-.651-.103-.897-.311-.248-.208-.422-.502-.521-.881l-1.096.452c.178.616.505 1.082.977 1.401.472.319.984.478 1.538.477a2.84 2.84 0 0 0 1.293-.291c.382-.193.684-.458.902-.794.218-.336.327-.72.327-1.149 0-.429-.115-.797-.344-1.105a2.067 2.067 0 0 0-.881-.689zm2.093-1.931l.602.913L15 10.045v5.744h1.187V8.446h-.827l-2.158 1.557zM22.105 0h-3.289v5.184H24V1.895A1.894 1.894 0 0 0 22.105 0zm-3.289 23.5l4.684-4.684h-4.684V23.5zM0 22.105C0 23.152.848 24 1.895 24h3.289v-5.184H0v3.289z'
    ),
    "telegram": (
        'M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z'
    ),
    "github": (
        'M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12'
    ),
    # Instructure, Canvas's maker -- Canvas itself has no separate simple-icons mark
    "canvas": (
        'm11.996 0-5.11 2.878L12 5.76l5.115-2.878ZM6.032 3.36.918 6.237 6.036 9.12l5.115-2.879Zm11.929 0-5.112 2.878 5.115 2.882 5.118-2.879zM12 11.52.918 17.76 12 24l11.082-6.241Z'
    ),
    "obsidian_vault": (
        'M19.355 18.538a68.967 68.959 0 0 0 1.858-2.954.81.81 0 0 0-.062-.9c-.516-.685-1.504-2.075-2.042-3.362-.553-1.321-.636-3.375-.64-4.377a1.707 1.707 0 0 0-.358-1.05l-3.198-4.064a3.744 3.744 0 0 1-.076.543c-.106.503-.307 1.004-.536 1.5-.134.29-.29.6-.446.914l-.31.626c-.516 1.068-.997 2.227-1.132 3.59-.124 1.26.046 2.73.815 4.481.128.011.257.025.386.044a6.363 6.363 0 0 1 3.326 1.505c.916.79 1.744 1.922 2.415 3.5zM8.199 22.569c.073.012.146.02.22.02.78.024 2.095.092 3.16.29.87.16 2.593.64 4.01 1.055 1.083.316 2.198-.548 2.355-1.664.114-.814.33-1.735.725-2.58l-.01.005c-.67-1.87-1.522-3.078-2.416-3.849a5.295 5.295 0 0 0-2.778-1.257c-1.54-.216-2.952.19-3.84.45.532 2.218.368 4.829-1.425 7.531zM5.533 9.938c-.023.1-.056.197-.098.29L2.82 16.059a1.602 1.602 0 0 0 .313 1.772l4.116 4.24c2.103-3.101 1.796-6.02.836-8.3-.728-1.73-1.832-3.081-2.55-3.831zM9.32 14.01c.615-.183 1.606-.465 2.745-.534-.683-1.725-.848-3.233-.716-4.577.154-1.552.7-2.847 1.235-3.95.113-.235.223-.454.328-.664.149-.297.288-.577.419-.86.217-.47.379-.885.46-1.27.08-.38.08-.72-.014-1.043-.095-.325-.297-.675-.68-1.06a1.6 1.6 0 0 0-1.475.36l-4.95 4.452a1.602 1.602 0 0 0-.513.952l-.427 2.83c.672.59 2.328 2.316 3.335 4.711.09.21.175.43.253.653z'
    ),
    "slack": (
        'M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z'
    ),
}

# Generic pulse glyph, deliberately not a fabricated Google Health/Fit
# logo -- no such mark exists in the simple-icons source used above.
# Also the fallback for any connector added later without its own icon yet.
_GENERIC_CONNECTOR_ICON_PATH = "M3 12h4l2-7 4 14 2-7h6"

# BrowserOS isn't in simple-icons (a small, new open-source project, not a
# simple-icons-curated brand) -- its real mark instead, fetched straight
# from its own repo (browseros-ai/BrowserOS, packages/browseros/resources/
# browseros/icons/product_logo.svg), Illustrator export cruft (the
# <switch>/<foreignObject> fallback wrapper, explicit width/height) trimmed
# since CSS already sizes every connector-icon. Unlike every path above,
# this is the real multi-color mark, not a single currentColor-tinted
# silhouette -- simple-icons ships those pre-flattened to one path; this
# project doesn't, and hand-flattening it would drift from what
# browseros.com actually shows.
_BROWSEROS_ICON_MARKUP = Markup(
    '<svg class="connector-icon connector-icon-browseros" viewBox="-52.978 -64.964 1024 1024" aria-hidden="true">'
    '<g><path fill="#fff" d="M910.053 738.831c0 94.563-76.659 171.222-171.222 171.222h-567.61C76.658 910.053 0 833.394 0 738.831v-567.61C0 76.658 76.658 0 171.221 0h567.61c94.563 0 171.222 76.658 171.222 171.221z"/>'
    '<path fill="#b3b3b3" d="M488.845 635.875S530.5 740.187 649.187 910.053c4.675-145.165-10.327-214.172-10.327-214.172" opacity=".34"/>'
    '<path fill="#fb651f" d="M527.673 374.229c-.261-15.189-12.783-27.291-27.975-27.028-15.188.262-27.288 12.788-27.024 27.977.262 15.188 12.788 27.289 27.976 27.024 15.19-.262 27.287-12.786 27.023-27.973M738.831 0h-567.61C76.658 0 0 76.658 0 171.221v567.61c0 63.994 35.113 119.779 87.119 149.165 53.412-27.822 131.499-80.394 165.635-169.817 13.041 11.689 146.516-16.221 130.547-155.313-5.69-64.09-51.765-130.233-67.098-168.479-15.333-38.249-15.711-60.248 2.89-98.165 13.29-26.822.425-28.435-10.385-17.242l-2.118 1.259s-73.652 53.847 3.019 174.78c76.67 120.938 55.979 197.104-39.966 234.212-58.415 15.684-116.708-32.216-125.684-56.512-8.935-21.855-15.825 3.942-11.82 23.435-26.808 5.35-90.872-93.799-24.958-170.739 30.005-32.303 39.214-65.474 38.561-103.365-.654-37.891 9.44-90.637 82.141-128.572 0 0-16.045-8.588-33.398-4.619-17.355 3.968-.618-35.75 74.218-56.299 15.584-.27 27.375-7.809 47.24-25.575s59.441-62.465 120.514-30.507c50.008 28.478 53.896 41.249 73.067 36.333 16.425-4.868-17.622-11.618-15.174-29.08 2.449-17.466 72.179-15.003 96.571 16.668 24.387 31.675 31.751 33.38 54.781 39.402 23.027 6.02 50.7 15.629 65.117 53.892 3.84 10.021-8.203 2.892-8.203 2.892s-35.043-11.315-14.431 14.004c20.614 25.319 36.356 34.216 33.652 90.199-2.699 55.979 6.499 57.656 43.277 63.439 36.776 5.787 69.828 7.961 70.761 62.049.937 54.084-11.092 101.056-35.781 105.153-24.685 4.095-35.019-10.4-35.193-20.484 0 0 7.947-17.56 15.767-42.453 11.888-2.038 34.394-26.269 34.807-55.616.406-29.349-22.416-23.458-52.68-23.847-30.269-.396-67.854.251-57.217 32.162 10.624 30.993 36.525 44.301 45.692 44.142-4.845 37.678-65.633 75.409-129.298 52.668-63.661-22.743-69.398-36.397-78.691-43.572-9.295-7.172-18.303 2.152-7.856 23.061 10.448 20.907 88.767 95.662 212.437 35.757 0 0 10.73 12.986 22.694 15.529-5.071 24.846-51.265 72.222-119.226 76.004-66.932 3.72-131.516-22.486-156.935-60.563-18.521-10.681 3.168 78.535 121.641 87.763-2.188 52.719 9.907 136.604 33.151 198.075h89.645c94.563 0 171.222-76.659 171.222-171.222v-567.61C910.053 76.658 833.394 0 738.831 0m-60.03 364.281c-.263-15.189-12.788-27.288-27.976-27.025-15.191.262-27.287 12.787-27.023 27.974.261 15.188 12.783 27.289 27.975 27.026 15.189-.262 27.286-12.787 27.024-27.975"/>'
    '<linearGradient id="browseros-a" x1="307.778" x2="883.829" y1="322.59" y2="1186.665" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#de4e12"/><stop offset="1" stop-color="#fb651f"/></linearGradient>'
    '<path fill="url(#browseros-a)" d="M316.202 394.386c-15.333-38.249-15.711-60.248 2.89-98.165 13.29-26.822.425-28.435-10.385-17.242l-2.118 1.259s-73.652 53.847 3.019 174.78c76.67 120.938 55.979 197.104-39.966 234.212-58.415 15.684-116.708-32.216-125.684-56.512-8.935-21.855-15.825 3.942-11.82 23.435-9.123 1.82-22.557-8.468-33.718-25.856l89.976 185.166c26.039-26.04 49.412-58.129 64.358-97.284 13.041 11.689 146.516-16.221 130.547-155.313-5.691-64.091-51.766-130.234-67.099-168.48m335.574-2.131c15.189-.262 27.286-12.786 27.024-27.975s-12.788-27.288-27.976-27.025c-15.191.262-27.287 12.787-27.023 27.974.262 15.189 12.784 27.289 27.975 27.026m70.061-31.469c-2.699 55.979 6.499 57.656 43.277 63.439 11.314 1.78 22.274 3.221 32.058 5.935l-85.65-173.902c.777 6.054-8.906.325-8.906.325s-35.043-11.315-14.431 14.004c20.614 25.319 36.356 34.217 33.652 90.199m109.023 90.54-4.138-1.835c5.537 8.43 8.865 20.148 9.152 36.783.937 54.084-11.092 101.056-35.781 105.153-24.685 4.095-35.019-10.4-35.193-20.484 0 0 7.947-17.56 15.767-42.453 11.888-2.038 34.394-26.269 34.807-55.616.406-29.349-22.416-23.458-52.68-23.847-30.269-.396-67.854.251-57.217 32.162 10.624 30.993 36.525 44.301 45.692 44.142-4.845 37.678-65.633 75.409-129.298 52.668-63.661-22.743-69.398-36.397-78.691-43.572-9.295-7.172-18.303 2.152-7.856 23.061 10.448 20.907 88.767 95.662 212.437 35.757 0 0 10.73 12.986 22.694 15.529-5.071 24.846-51.265 72.222-119.226 76.004-66.932 3.72-131.516-22.486-156.935-60.563-18.521-10.681 3.168 78.535 121.641 87.763-2.188 52.719 9.907 136.604 33.151 198.075h89.645c94.563 0 171.222-76.659 171.222-171.222V612.117zm-303.187-77.097c-.261-15.189-12.783-27.291-27.975-27.028-15.188.262-27.288 12.788-27.024 27.977.262 15.188 12.788 27.289 27.976 27.024 15.19-.262 27.287-12.786 27.023-27.973M77.804 587.87l8.032 16.529a132 132 0 0 1-5.314-19.711z" opacity=".71"/>'
    '</g></svg>'
)


def _connector_icon(connector: str) -> Markup:
    if connector == "browseros":
        return _BROWSEROS_ICON_MARKUP
    path = _CONNECTOR_ICON_PATHS.get(connector)
    if path is None:
        return Markup(
            '<svg class="connector-icon connector-icon-generic" viewBox="0 0 24 24" '
            'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true"><path d="{_GENERIC_CONNECTOR_ICON_PATH}"/></svg>'
        )
    return Markup(
        f'<svg class="connector-icon" viewBox="0 0 24 24" fill="currentColor" '
        f'aria-hidden="true"><path d="{path}"/></svg>'
    )


_STATUS_LABELS = {
    "ok": "Connected",
    "error": "Disconnected",
    "stale": "Stale",
    "never_synced": "Never connected",
}


def _status_label(state: str) -> str:
    return _STATUS_LABELS.get(state, state.replace("_", " ").title())


# audit.py's AuditEvent.outcome is a plain str, not a closed enum -- every
# caller across the codebase (jobs, gmail_inbound, models, slack, telegram_runtime,
# ...) picks its own outcome string, so this list is every value actually
# produced today, not a spec. A new caller's new outcome string still renders
# correctly (falls back to title-cased text below) -- it just won't get a
# curated label until added here.
_OUTCOME_LABELS = {
    "ok": "Success",
    "sent": "Sent",
    "handled": "Handled",
    "outbox_enqueued": "Queued",
    "duplicate": "Duplicate",
    "ignored": "Ignored",
    "error": "Error",
    "failed": "Failed",
    "rejected": "Rejected",
    "refused": "Refused",
}


def _outcome_label(outcome: str) -> str:
    return _OUTCOME_LABELS.get(outcome, outcome.replace("_", " ").title())


def _rate(value: float | None) -> str:
    """Render a ratio as a percentage, distinguishing "none yet" from zero.

    An em dash rather than 0% when nothing has been measured: a fresh install
    with no feedback votes has not scored 0, it has not been scored at all,
    and showing 0% would read as a failing system.
    """
    if value is None:
        return "—"
    return f"{round(value * 100)}%"


_env.filters["dt"] = _format_dt
_env.filters["connector_icon"] = _connector_icon
_env.filters["status_label"] = _status_label
_env.filters["outcome_label"] = _outcome_label
_env.filters["rate"] = _rate


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
        # browseros_health() is a live probe, not a sync_state row -- see its
        # module docstring for why it can't come out of connector_health()
        # itself. Appended last so sync-tracked connectors keep their stable
        # order and this one shot doesn't reshuffle the page on every load.
        connectors = [*connector_health(database), browseros_health()]
        return _render("connectors.html", active="connectors", connectors=connectors)

    async def evaluation_page(request: Request) -> Response:
        return _render(
            "evaluation.html",
            active="evaluation",
            report=EvaluationService(database).report(),
        )

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
            Route("/evaluation", evaluation_page),
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
