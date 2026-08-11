"""Local default-deny client scopes and one-time approval tokens."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .audit import AuditEvent, AuditLog
from .db import Database


class PolicyError(PermissionError):
    """Raised when a client or approval does not satisfy Alfred's policy."""


class ClientScope(BaseModel):
    client_id: str
    allowed_sensitivities: set[str] = Field(default_factory=set)
    allowed_tools: set[str] = Field(default_factory=set)
    allow_write: bool = False
    active: bool = True


class Approval(BaseModel):
    id: str
    actor: str
    action_type: str
    preview: dict[str, Any]
    state: str
    requested_at: datetime
    expires_at: datetime
    approved_at: datetime | None
    approved_by: str | None
    consumed_at: datetime | None


class IssuedApproval(BaseModel):
    approval: Approval
    token: str


class PolicyStore:
    """Persistent client identity policy. Unregistered clients have no access."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def grant(
        self,
        *,
        client_id: str,
        allowed_sensitivities: set[str],
        allowed_tools: set[str],
        allow_write: bool = False,
        actor: str = "user:cli",
    ) -> ClientScope:
        if not client_id.strip():
            raise PolicyError("client ID cannot be empty")
        if not allowed_sensitivities <= {"public", "personal", "sensitive", "secret"}:
            raise PolicyError("client scope contains an unknown sensitivity")
        self.database.migrate()
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    INSERT INTO client_scopes (
                        client_id, allowed_sensitivities_json, allowed_tools_json, allow_write,
                        active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(client_id) DO UPDATE SET
                        allowed_sensitivities_json = excluded.allowed_sensitivities_json,
                        allowed_tools_json = excluded.allowed_tools_json,
                        allow_write = excluded.allow_write,
                        active = 1,
                        updated_at = excluded.updated_at
                    """,
                    (
                        client_id,
                        self._json(sorted(allowed_sensitivities)),
                        self._json(sorted(allowed_tools)),
                        int(allow_write),
                        now,
                        now,
                    ),
                )
                scope = self._scope_from_row(
                    connection.execute("SELECT * FROM client_scopes WHERE client_id = ?", (client_id,)).fetchone()
                )
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor=actor,
                        client="policy",
                        tool="client_scope_grant",
                        outcome="ok",
                        result={"client_id": client_id, "allow_write": allow_write},
                    ),
                )
                return scope

    def scope_for(self, client_id: str) -> ClientScope:
        """Return an empty, inactive scope for every unregistered client."""
        self.database.migrate()
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM client_scopes WHERE client_id = ?", (client_id,)).fetchone()
        return self._scope_from_row(row) if row else ClientScope(client_id=client_id, active=False)

    def require_read(self, client_id: str, tool: str) -> ClientScope:
        scope = self.scope_for(client_id)
        if not scope.active or tool not in scope.allowed_tools:
            raise PolicyError(f"client is not allowed to use {tool}")
        return scope

    def require_write(self, client_id: str, tool: str) -> ClientScope:
        scope = self.require_read(client_id, tool)
        if not scope.allow_write:
            raise PolicyError(f"client is not allowed to write through {tool}")
        return scope

    @staticmethod
    def _scope_from_row(row: sqlite3.Row) -> ClientScope:
        return ClientScope(
            client_id=row["client_id"],
            allowed_sensitivities=set(json.loads(row["allowed_sensitivities_json"])),
            allowed_tools=set(json.loads(row["allowed_tools_json"])),
            allow_write=bool(row["allow_write"]),
            active=bool(row["active"]),
        )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))


class ApprovalService:
    """Issue and consume fresh local approvals without persisting raw tokens."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def propose(
        self,
        *,
        actor: str,
        action_type: str,
        preview: dict[str, Any],
        ttl: timedelta = timedelta(minutes=15),
        now: datetime | None = None,
    ) -> Approval:
        if ttl <= timedelta(0):
            raise PolicyError("approval TTL must be positive")
        requested_at = (now or datetime.now(UTC)).astimezone(UTC)
        approval_id = str(uuid4())
        approval = Approval(
            id=approval_id,
            actor=actor,
            action_type=action_type,
            preview=preview,
            state="pending",
            requested_at=requested_at,
            expires_at=requested_at + ttl,
            approved_at=None,
            approved_by=None,
            consumed_at=None,
        )
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    INSERT INTO approvals (id, requested_at, expires_at, actor, action_type, preview_json, state)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        approval.id,
                        approval.requested_at.isoformat(),
                        approval.expires_at.isoformat(),
                        actor,
                        action_type,
                        self._json(preview),
                    ),
                )
                self._audit(connection, actor, "approval_propose", {"approval_id": approval.id, "action_type": action_type})
        return approval

    def approve(self, approval_id: str, *, actor: str, now: datetime | None = None) -> IssuedApproval:
        """Approve only the original requester and issue one fresh raw token once."""
        approved_at = (now or datetime.now(UTC)).astimezone(UTC)
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                row = self._load_for_change(connection, approval_id, approved_at)
                if row["actor"] != actor:
                    raise PolicyError("only the requesting actor can approve this action")
                if row["state"] != "pending":
                    raise PolicyError(f"approval is not pending: {row['state']}")
                token = secrets.token_urlsafe(32)
                connection.execute(
                    """
                    UPDATE approvals
                    SET state = 'approved', token_hash = ?, approved_at = ?, approved_by = ?
                    WHERE id = ? AND state = 'pending'
                    """,
                    (self._token_hash(token), approved_at.isoformat(), actor, approval_id),
                )
                approved = self._approval_from_row(
                    connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
                )
                self._audit(connection, actor, "approval_approve", {"approval_id": approval_id})
                return IssuedApproval(approval=approved, token=token)

    def consume(self, approval_id: str, *, actor: str, token: str, now: datetime | None = None) -> Approval:
        """Consume an unexpired token exactly once before executing a consequential action."""
        consumed_at = (now or datetime.now(UTC)).astimezone(UTC)
        self.database.migrate()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                row = self._load_for_change(connection, approval_id, consumed_at)
                if row["actor"] != actor:
                    raise PolicyError("approval actor does not match the requested action")
                if row["state"] != "approved":
                    raise PolicyError(f"approval is not ready to consume: {row['state']}")
                if not secrets.compare_digest(row["token_hash"] or "", self._token_hash(token)):
                    raise PolicyError("approval token is invalid")
                changed = connection.execute(
                    """
                    UPDATE approvals
                    SET state = 'consumed', consumed_at = ?
                    WHERE id = ? AND state = 'approved' AND token_hash = ?
                    """,
                    (consumed_at.isoformat(), approval_id, self._token_hash(token)),
                ).rowcount
                if changed != 1:
                    raise PolicyError("approval was already consumed")
                consumed = self._approval_from_row(
                    connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
                )
                self._audit(connection, actor, "approval_consume", {"approval_id": approval_id})
                return consumed

    def verify(self, approval_id: str, *, actor: str, token: str) -> Approval:
        """Read-only token check that never changes state.

        For connectors whose ``execute()`` must replay an idempotent action:
        the first execute() call already consumed the token, so a retry can't
        call consume() again, but it still must not skip authentication.
        """
        self.database.migrate()
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise PolicyError("approval does not exist")
        if row["actor"] != actor:
            raise PolicyError("approval actor does not match the requested action")
        if row["state"] not in {"approved", "consumed"}:
            raise PolicyError(f"approval is not usable: {row['state']}")
        if not secrets.compare_digest(row["token_hash"] or "", self._token_hash(token)):
            raise PolicyError("approval token is invalid")
        return self._approval_from_row(row)

    def _load_for_change(self, connection: sqlite3.Connection, approval_id: str, now: datetime) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise PolicyError("approval does not exist")
        if row["state"] in {"pending", "approved"} and datetime.fromisoformat(row["expires_at"]) <= now:
            connection.execute("UPDATE approvals SET state = 'expired' WHERE id = ?", (approval_id,))
            raise PolicyError("approval has expired")
        return row

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> Approval:
        return Approval(
            id=row["id"],
            actor=row["actor"],
            action_type=row["action_type"],
            preview=json.loads(row["preview_json"]),
            state=row["state"],
            requested_at=datetime.fromisoformat(row["requested_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            approved_at=datetime.fromisoformat(row["approved_at"]) if row["approved_at"] else None,
            approved_by=row["approved_by"],
            consumed_at=datetime.fromisoformat(row["consumed_at"]) if row["consumed_at"] else None,
        )

    @staticmethod
    def _audit(connection: sqlite3.Connection, actor: str, tool: str, result: dict[str, str]) -> None:
        AuditLog.append_in_transaction(
            connection,
            AuditEvent(actor=actor, client="policy", tool=tool, outcome="ok", result=result),
        )
