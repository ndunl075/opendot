"""Overflow app tools via Composio's free cloud API.

Alfred already owns Gmail, Calendar, GitHub, Slack, Telegram, and Google
Health with local credentials and the section 8 approval boundary. Composio
is the rest of the catalog -- Notion, Spotify, Linear, and so on -- without
standing up an OAuth app per provider. The API key lives in the OS keyring;
connected-account tokens stay on Composio's side. Writes still preview and
wait for a human. Hermes never talks to Composio's hosted MCP URL: that
path skips Alfred's modifiers and would auto-approve under YOLO.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from .audit import AuditEvent, AuditLog
from .db import Database
from .models import Redactor
from .policy import Approval, ApprovalService, PolicyError

API_BASE = "https://backend.composio.dev/api/v3.1"
SECRET_NAME = "composio-api-key"
USER_ID = "alfred"
# New signups from 15 Aug 2026: 100k tool calls/mo, hard-capped, no card.
# Grandfathered Hobby plans were 20k; override with ALFRED_COMPOSIO_MONTHLY_CALL_LIMIT.
DEFAULT_MONTHLY_CALL_LIMIT = 100_000
CONNECTOR_NAME = "composio"
ACTION_TYPE = "composio_tool_execute"

# First-party Alfred connectors. Asking Composio for these would duplicate
# auth, skip local sync, and let a write around the dedicated approval tools.
RESERVED_TOOLKITS = frozenset(
    {
        "gmail",
        "googlecalendar",
        "google_calendar",
        "googledrive",
        "github",
        "slack",
        "telegram",
        "fitbit",
        "googlefit",
        "google_fit",
        "googlehealth",
        "google_health",
    }
)

_WRITE_TOKENS = frozenset(
    {
        "ADD",
        "ARCHIVE",
        "ASSIGN",
        "CANCEL",
        "CLOSE",
        "COMMENT",
        "CREATE",
        "DELETE",
        "DISABLE",
        "DROP",
        "EDIT",
        "ENABLE",
        "FOLLOW",
        "GRANT",
        "INSERT",
        "INVITE",
        "KILL",
        "MOVE",
        "PATCH",
        "POST",
        "PUBLISH",
        "PUT",
        "REMOVE",
        "REPLY",
        "REVOKE",
        "SEND",
        "SET",
        "SHARE",
        "STAR",
        "SUBSCRIBE",
        "UNSUBSCRIBE",
        "UPDATE",
        "UPLOAD",
        "WRITE",
    }
)
_READ_TOKENS = frozenset(
    {
        "CHECK",
        "COUNT",
        "DESCRIBE",
        "DOWNLOAD",
        "FETCH",
        "FIND",
        "GET",
        "LIST",
        "LOOKUP",
        "QUERY",
        "READ",
        "RETRIEVE",
        "SEARCH",
        "SHOW",
        "STATUS",
        "VIEW",
    }
)


class ComposioError(RuntimeError):
    """A Composio API or policy failure safe to surface to the operator."""


class ComposioQuotaExceeded(ComposioError):
    """Local count of this UTC month's calls reached the free-tier cap."""


class ComposioReservedToolkit(ComposioError):
    """Caller asked Composio for an app Alfred already owns first-party."""


class ComposioTool(BaseModel):
    slug: str
    name: str
    description: str
    toolkit: str
    tags: tuple[str, ...] = ()
    no_auth: bool = False
    writes: bool = False
    input_fields: tuple[str, ...] = ()


class ComposioAccount(BaseModel):
    id: str
    toolkit: str
    status: str
    alias: str | None = None


class ComposioConnectLink(BaseModel):
    toolkit: str
    redirect_url: str
    expires_at: str | None = None
    connected_account_id: str | None = None


class ComposioStatus(BaseModel):
    configured: bool
    user_id: str = USER_ID
    monthly_limit: int
    calls_this_month: int
    remaining: int
    accounts: list[ComposioAccount] = Field(default_factory=list)


class ComposioReceipt(BaseModel):
    slug: str
    successful: bool
    data: Any = None
    error: str | None = None
    idempotency_key: str
    replayed: bool = False


class ComposioTransport(Protocol):
    def search_tools(self, query: str, *, toolkit: str | None = None, limit: int = 10) -> list[ComposioTool]: ...

    def get_tool(self, slug: str) -> ComposioTool: ...

    def execute(self, slug: str, arguments: dict[str, Any]) -> dict[str, Any]: ...

    def list_accounts(self) -> list[ComposioAccount]: ...

    def connect(self, toolkit: str) -> ComposioConnectLink: ...


def monthly_call_limit() -> int:
    raw = os.environ.get("ALFRED_COMPOSIO_MONTHLY_CALL_LIMIT", "").strip()
    if not raw:
        return DEFAULT_MONTHLY_CALL_LIMIT
    try:
        value = int(raw)
    except ValueError as error:
        raise ComposioError("ALFRED_COMPOSIO_MONTHLY_CALL_LIMIT must be a positive integer") from error
    if value <= 0:
        raise ComposioError("ALFRED_COMPOSIO_MONTHLY_CALL_LIMIT must be a positive integer")
    return value


def normalize_toolkit(toolkit: str) -> str:
    slug = toolkit.strip().lower().replace(" ", "").replace("-", "")
    if slug == "googlecalendar":
        return "googlecalendar"
    return toolkit.strip().lower().replace(" ", "_")


def assert_overflow_toolkit(toolkit: str) -> str:
    slug = normalize_toolkit(toolkit)
    compact = slug.replace("_", "")
    reserved = {name.replace("_", "") for name in RESERVED_TOOLKITS}
    if compact in reserved:
        raise ComposioReservedToolkit(
            f"{slug} is a first-party Alfred connector; use the dedicated tools, not Composio"
        )
    if not slug:
        raise ComposioError("toolkit slug cannot be empty")
    return slug


def tool_writes(slug: str, tags: tuple[str, ...] = ()) -> bool:
    """True when executing this tool would change something outside Alfred.

    Unknown slugs default to write so a new destructive tool cannot sneak
    through as a live MCP call. Tags named 'read' only win when no write
    tag is present.
    """
    tokens = {part for part in re.split(r"[^A-Za-z]+", slug.upper()) if part}
    lowered = {tag.lower() for tag in tags}
    if tokens & _WRITE_TOKENS or lowered & {"write", "destructive", "create", "delete"}:
        return True
    if tokens & _READ_TOKENS or lowered & {"read"}:
        return False
    return True


def _month_start_utc() -> str:
    now = datetime.now(UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC).isoformat()


def calls_this_month(database: Database) -> int:
    database.migrate()
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS n FROM tool_runs
            WHERE tool LIKE 'composio_%' AND occurred_at >= ?
            """,
            (_month_start_utc(),),
        ).fetchone()
    return int(row["n"]) if row else 0


class ComposioClient:
    """httpx client for Composio REST v3.1. No SDK, so tests stay on MockTransport."""

    def __init__(
        self,
        api_key: str,
        *,
        database: Database | None = None,
        transport: httpx.BaseTransport | None = None,
        user_id: str = USER_ID,
        monthly_limit: int | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Composio API key must not be empty")
        self.user_id = user_id
        self.database = database
        self.monthly_limit = monthly_limit if monthly_limit is not None else monthly_call_limit()
        self._client = httpx.Client(
            base_url=API_BASE,
            headers={"x-api-key": api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(30.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def search_tools(self, query: str, *, toolkit: str | None = None, limit: int = 10) -> list[ComposioTool]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 50))}
        cleaned = query.strip()
        if cleaned:
            params["query"] = cleaned
        if toolkit:
            params["toolkit_slug"] = assert_overflow_toolkit(toolkit)
        payload = self._request("GET", "/tools", params=params, audit_tool="composio_search")
        return [_parse_tool(item) for item in _items(payload) if _tool_toolkit(item) not in RESERVED_TOOLKITS]

    def get_tool(self, slug: str) -> ComposioTool:
        payload = self._request("GET", f"/tools/{slug.strip()}", audit_tool="composio_get_tool")
        tool = _parse_tool(payload if "slug" in payload else payload.get("item") or payload)
        assert_overflow_toolkit(tool.toolkit)
        return tool

    def execute(self, slug: str, arguments: dict[str, Any]) -> dict[str, Any]:
        cleaned = slug.strip()
        if not cleaned:
            raise ComposioError("tool slug cannot be empty")
        assert_overflow_toolkit(cleaned.split("_", 1)[0].lower())
        body = {
            "arguments": arguments,
            "user_id": self.user_id,
            "dangerously_skip_version_check": True,
        }
        return self._request(
            "POST",
            f"/tools/execute/{cleaned}",
            json_body=body,
            audit_tool="composio_execute",
            extra_arguments={"slug": cleaned},
        )

    def list_accounts(self) -> list[ComposioAccount]:
        payload = self._request(
            "GET",
            "/connected_accounts",
            params=[("user_ids", self.user_id), ("limit", "50")],
            audit_tool="composio_accounts",
        )
        accounts: list[ComposioAccount] = []
        for item in _items(payload):
            toolkit = str((item.get("toolkit") or {}).get("slug") or "")
            if not toolkit:
                continue
            accounts.append(
                ComposioAccount(
                    id=str(item.get("id") or ""),
                    toolkit=toolkit,
                    status=str(item.get("status") or ""),
                    alias=item.get("alias") if isinstance(item.get("alias"), str) else None,
                )
            )
        return accounts

    def connect(self, toolkit: str) -> ComposioConnectLink:
        slug = assert_overflow_toolkit(toolkit)
        auth_config_id = self._auth_config_id(slug)
        payload = self._request(
            "POST",
            "/connected_accounts/link",
            json_body={"auth_config_id": auth_config_id, "user_id": self.user_id},
            audit_tool="composio_connect",
            extra_arguments={"toolkit": slug},
        )
        redirect = payload.get("redirect_url")
        if not isinstance(redirect, str) or not redirect.strip():
            raise ComposioError("Composio did not return a connect URL")
        return ComposioConnectLink(
            toolkit=slug,
            redirect_url=redirect,
            expires_at=payload.get("expires_at") if isinstance(payload.get("expires_at"), str) else None,
            connected_account_id=(
                payload.get("connected_account_id")
                if isinstance(payload.get("connected_account_id"), str)
                else None
            ),
        )

    def _auth_config_id(self, toolkit: str) -> str:
        listed = self._request(
            "GET",
            "/auth_configs",
            params={"toolkit_slug": toolkit, "limit": 10},
            audit_tool="composio_auth_configs",
            extra_arguments={"toolkit": toolkit},
        )
        for item in _items(listed):
            config_id = item.get("id")
            status = str(item.get("status") or "ENABLED").upper()
            if isinstance(config_id, str) and config_id.strip() and status != "DISABLED":
                return config_id
        created = self._request(
            "POST",
            "/auth_configs",
            json_body={
                "toolkit": {"slug": toolkit},
                "auth_config": {"type": "use_composio_managed_auth", "name": f"alfred-{toolkit}"},
            },
            audit_tool="composio_auth_config_create",
            extra_arguments={"toolkit": toolkit},
        )
        nested = created.get("auth_config") if isinstance(created.get("auth_config"), dict) else created
        config_id = nested.get("id") if isinstance(nested, dict) else None
        if not isinstance(config_id, str) or not config_id.strip():
            raise ComposioError(f"Composio did not create an auth config for {toolkit}")
        return config_id

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json_body: dict[str, Any] | None = None,
        audit_tool: str,
        extra_arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._enforce_quota()
        try:
            response = self._client.request(method, path, params=params, json=json_body)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ComposioError(_http_error_message(error)) from error
        except httpx.HTTPError as error:
            raise ComposioError(f"Composio request failed: {error.__class__.__name__}") from error
        payload = response.json()
        if not isinstance(payload, dict):
            raise ComposioError("Composio returned a non-object response")
        self._record_call(audit_tool, extra_arguments or {})
        return payload

    def _enforce_quota(self) -> None:
        if self.database is None:
            return
        used = calls_this_month(self.database)
        if used >= self.monthly_limit:
            raise ComposioQuotaExceeded(
                f"Composio free-tier cap reached ({used}/{self.monthly_limit} calls this UTC month). "
                "Wait for next month or raise ALFRED_COMPOSIO_MONTHLY_CALL_LIMIT."
            )

    def _record_call(self, tool: str, arguments: dict[str, Any]) -> None:
        if self.database is None:
            return
        redacted = Redactor().redact(json.dumps(arguments, sort_keys=True))
        try:
            safe_arguments = json.loads(redacted)
        except json.JSONDecodeError:
            safe_arguments = {"redacted": True}
        AuditLog(self.database).append(
            AuditEvent(
                actor="system:composio",
                client="composio",
                tool=tool,
                outcome="ok",
                arguments=safe_arguments if isinstance(safe_arguments, dict) else {},
                result={"counted": True},
            )
        )


class ComposioActions:
    """Search/connect/read now; writes go through the same preview/approve path."""

    connector_name = CONNECTOR_NAME
    action_type = ACTION_TYPE

    def __init__(
        self,
        database: Database,
        approvals: ApprovalService,
        transport: ComposioTransport | None = None,
    ) -> None:
        self.database = database
        self.approvals = approvals
        self.transport = transport

    def search(self, query: str, *, toolkit: str | None = None) -> list[ComposioTool]:
        return self._transport().search_tools(query, toolkit=toolkit)

    def status(self) -> ComposioStatus:
        limit = monthly_call_limit()
        used = calls_this_month(self.database)
        accounts = self._transport().list_accounts()
        return ComposioStatus(
            configured=True,
            monthly_limit=limit,
            calls_this_month=used,
            remaining=max(0, limit - used),
            accounts=accounts,
        )

    def connect(self, toolkit: str) -> ComposioConnectLink:
        return self._transport().connect(toolkit)

    def execute_or_propose(
        self, *, actor: str, slug: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Run a read immediately; return an approval preview for a write."""
        args = arguments or {}
        tool = self._transport().get_tool(slug)
        if tool.writes:
            approval = self.propose(actor=actor, slug=tool.slug, arguments=args, tool=tool)
            return {
                "needs_approval": True,
                "approval": approval.model_dump(mode="json"),
                "tool": tool.model_dump(mode="json"),
            }
        receipt = self._execute_now(slug=tool.slug, arguments=args)
        return {"needs_approval": False, "result": receipt.model_dump(mode="json"), "tool": tool.model_dump(mode="json")}

    def propose(
        self,
        *,
        actor: str,
        slug: str,
        arguments: dict[str, Any],
        tool: ComposioTool | None = None,
    ) -> Approval:
        resolved = tool or self._transport().get_tool(slug)
        if not resolved.writes:
            raise ComposioError(f"{resolved.slug} is a read; call it directly instead of proposing")
        return self.approvals.propose(
            actor=actor,
            action_type=self.action_type,
            preview={
                "slug": resolved.slug,
                "toolkit": resolved.toolkit,
                "name": resolved.name,
                "arguments": arguments,
            },
        )

    def execute(self, approval_id: str, *, actor: str, token: str) -> ComposioReceipt:
        self.database.migrate()
        idempotency_key = f"{self.action_type}:{approval_id}"
        approval = self.approvals.verify(approval_id, actor=actor, token=token)
        if approval.action_type != self.action_type:
            raise PolicyError("approval is not for a Composio tool call")
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT actor, payload_json FROM action_receipts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if existing is not None:
            self.approvals.verify(approval_id, actor=actor, token=token)
            if existing["actor"] != actor:
                raise PolicyError("approval actor does not match the requested action")
            payload = json.loads(existing["payload_json"])
            return ComposioReceipt(
                slug=payload["slug"],
                successful=payload.get("successful", True),
                data=payload.get("data"),
                error=payload.get("error"),
                idempotency_key=idempotency_key,
                replayed=True,
            )
        preview = approval.preview
        slug = str(preview["slug"])
        arguments = preview.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("Composio approval preview is missing arguments")
        if approval.state != "consumed":
            self.approvals.consume(approval_id, actor=actor, token=token)
        result = self._transport().execute(slug, arguments)
        receipt = _receipt_from_payload(slug, result, idempotency_key)
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    INSERT INTO action_receipts (
                        idempotency_key, connector, action_type, approval_id, actor, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        idempotency_key,
                        self.connector_name,
                        self.action_type,
                        approval_id,
                        actor,
                        json.dumps(
                            {
                                "slug": receipt.slug,
                                "successful": receipt.successful,
                                "data": receipt.data,
                                "error": receipt.error,
                            },
                            sort_keys=True,
                        ),
                        datetime.now(UTC).isoformat(),
                    ),
                )
        return receipt

    def _execute_now(self, *, slug: str, arguments: dict[str, Any]) -> ComposioReceipt:
        result = self._transport().execute(slug, arguments)
        return _receipt_from_payload(slug, result, f"live:{slug}")

    def _transport(self) -> ComposioTransport:
        if self.transport is None:
            raise ValueError("ComposioActions requires a transport to reach Composio")
        return self.transport


def _parse_tool(item: dict[str, Any]) -> ComposioTool:
    slug = str(item.get("slug") or "")
    if not slug:
        raise ComposioError("Composio tool is missing a slug")
    toolkit = _tool_toolkit(item)
    tags = tuple(str(tag) for tag in item.get("tags") or [] if isinstance(tag, str))
    schema = item.get("input_parameters") if isinstance(item.get("input_parameters"), dict) else {}
    required = schema.get("required") if isinstance(schema, dict) else None
    if isinstance(required, list):
        fields = tuple(str(name) for name in required if isinstance(name, str))
    else:
        properties = schema.get("properties") if isinstance(schema, dict) else None
        fields = tuple(sorted(properties)) if isinstance(properties, dict) else ()
    return ComposioTool(
        slug=slug,
        name=str(item.get("name") or slug),
        description=str(item.get("description") or item.get("human_description") or ""),
        toolkit=toolkit,
        tags=tags,
        no_auth=bool(item.get("no_auth")),
        writes=tool_writes(slug, tags),
        input_fields=fields,
    )


def _tool_toolkit(item: dict[str, Any]) -> str:
    toolkit = item.get("toolkit")
    if isinstance(toolkit, dict):
        return str(toolkit.get("slug") or "")
    if isinstance(toolkit, str):
        return toolkit
    return ""


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items")
    if items is None:
        return [payload] if "slug" in payload else []
    if not isinstance(items, list):
        raise ComposioError("Composio list response is not an array")
    return [item for item in items if isinstance(item, dict)]


def _receipt_from_payload(slug: str, payload: dict[str, Any], idempotency_key: str) -> ComposioReceipt:
    error = payload.get("error")
    error_text = None
    if isinstance(error, dict):
        error_text = str(error.get("message") or error)
    elif isinstance(error, str) and error.strip():
        error_text = error
    successful = payload.get("successful")
    if successful is None:
        successful = error_text is None
    return ComposioReceipt(
        slug=slug,
        successful=bool(successful),
        data=payload.get("data", payload.get("response")),
        error=error_text,
        idempotency_key=idempotency_key,
    )


def _http_error_message(error: httpx.HTTPStatusError) -> str:
    try:
        payload = error.response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        nested = payload.get("error")
        if isinstance(nested, dict) and isinstance(nested.get("message"), str):
            hint = nested.get("suggested_fix")
            message = nested["message"]
            if isinstance(hint, str) and hint.strip():
                return f"{message} ({hint})"
            return message
        if isinstance(payload.get("message"), str):
            return payload["message"]
    return f"Composio HTTP {error.response.status_code}"
