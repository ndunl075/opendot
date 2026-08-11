"""Local Telegram polling and outbox delivery with conservative failure handling."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel

from .audit import AuditEvent, AuditLog
from .db import Database
from .telegram import TelegramGateway, TelegramPair, TelegramUpdate


class TelegramTransport(Protocol):
    def get_updates(self, *, offset: int | None, timeout_seconds: int = 25) -> list[dict]: ...

    def send_message(self, *, chat_id: int, text: str) -> int: ...


class PollResult(BaseModel):
    received: int
    handled: int
    rejected: int
    cursor: int | None


class DeliveryResult(BaseModel):
    outbox_id: str
    state: str
    telegram_message_id: int | None = None
    error: str | None = None


class TelegramLongPoller:
    """Receive Bot API updates locally; replay safety comes from event idempotency."""

    connector_name = "telegram"
    account_name = "bot"

    def __init__(self, database: Database, transport: TelegramTransport, allowed_pairs: set[TelegramPair]) -> None:
        self.database = database
        self.transport = transport
        self.gateway = TelegramGateway(database, allowed_pairs)

    def poll_once(self, *, timeout_seconds: int = 25) -> PollResult:
        self.database.migrate()
        cursor = self._load_cursor()
        try:
            raw_updates = self.transport.get_updates(
                offset=cursor + 1 if cursor is not None else None,
                timeout_seconds=timeout_seconds,
            )
        except Exception as error:
            self._store_error(error.__class__.__name__)
            raise
        handled = 0
        rejected = 0
        latest_cursor = cursor
        for raw_update in sorted(raw_updates, key=lambda item: item.get("update_id", -1)):
            update_id = raw_update.get("update_id")
            if not isinstance(update_id, int):
                rejected += 1
                self._audit("telegram_update_rejected", {"reason": "missing update_id"})
                continue
            try:
                update = TelegramUpdate.model_validate(raw_update)
                self.gateway.handle(update)
                handled += 1
            except (ValueError, PermissionError) as error:
                rejected += 1
                self._audit("telegram_update_rejected", {"update_id": str(update_id), "reason": error.__class__.__name__})
            latest_cursor = max(latest_cursor, update_id) if latest_cursor is not None else update_id
            self._store_cursor(latest_cursor)
        if not raw_updates:
            self._store_success(cursor)
        return PollResult(received=len(raw_updates), handled=handled, rejected=rejected, cursor=latest_cursor)

    def _load_cursor(self) -> int | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT cursor FROM sync_state WHERE connector = ? AND account = ?",
                (self.connector_name, self.account_name),
            ).fetchone()
        return int(row["cursor"]) if row and row["cursor"] is not None else None

    def _store_cursor(self, cursor: int) -> None:
        self._store_success(cursor)

    def _store_success(self, cursor: int | None) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at)
                    VALUES (?, ?, ?, ?, NULL, ?)
                    ON CONFLICT(connector, account) DO UPDATE SET
                        cursor = excluded.cursor,
                        last_success_at = excluded.last_success_at,
                        last_error = NULL,
                        updated_at = excluded.updated_at
                    """,
                    (self.connector_name, self.account_name, str(cursor) if cursor is not None else None, now, now),
                )

    def _store_error(self, reason: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    """
                    INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at)
                    VALUES (?, ?, NULL, NULL, ?, ?)
                    ON CONFLICT(connector, account) DO UPDATE SET
                        last_error = excluded.last_error,
                        updated_at = excluded.updated_at
                    """,
                    (self.connector_name, self.account_name, reason, now),
                )

    def _audit(self, tool: str, result: dict[str, str]) -> None:
        AuditLog(self.database).append(
            AuditEvent(actor="system:telegram", client="telegram", tool=tool, outcome="rejected", result=result)
        )


class TelegramOutboxWorker:
    """Deliver only locally allowed Telegram messages; ambiguous failures never retry automatically."""

    destination_pattern = re.compile(r"^telegram:(-?\d+)$")

    def __init__(self, database: Database, transport: TelegramTransport, allowed_chat_ids: set[int]) -> None:
        self.database = database
        self.transport = transport
        self.allowed_chat_ids = allowed_chat_ids

    def deliver_pending(self, *, limit: int = 20) -> list[DeliveryResult]:
        self.database.migrate()
        results: list[DeliveryResult] = []
        for _ in range(limit):
            claimed = self._claim_next()
            if claimed is None:
                break
            outbox_id, destination, payload = claimed
            match = self.destination_pattern.fullmatch(destination)
            if match is None or int(match.group(1)) not in self.allowed_chat_ids:
                results.append(self._fail(outbox_id, "destination is not a locally allowed Telegram chat"))
                continue
            text = payload.get("text")
            if not isinstance(text, str):
                results.append(self._fail(outbox_id, "outbox payload has no text message"))
                continue
            try:
                message_id = self.transport.send_message(chat_id=int(match.group(1)), text=text)
            except Exception as error:
                # A timeout can still have delivered the message. Leave it failed for human review, never auto-retry.
                results.append(self._fail(outbox_id, f"Telegram send failed: {error.__class__.__name__}"))
                continue
            with self.database.connect() as connection:
                with self.database.transaction(connection):
                    connection.execute(
                        "UPDATE outbox SET state = 'sent', sent_at = ?, last_error = NULL WHERE id = ? AND state = 'sending'",
                        (datetime.now(UTC).isoformat(), outbox_id),
                    )
                    AuditLog.append_in_transaction(
                        connection,
                        AuditEvent(
                            actor="system:telegram",
                            client="telegram",
                            tool="telegram_send",
                            outcome="sent",
                            result={"outbox_id": outbox_id, "telegram_message_id": str(message_id)},
                            correlation_id=outbox_id,
                        ),
                    )
            results.append(DeliveryResult(outbox_id=outbox_id, state="sent", telegram_message_id=message_id))
        return results

    def _claim_next(self) -> tuple[str, str, dict] | None:
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                row = connection.execute(
                    "SELECT id, destination, payload_json FROM outbox WHERE state = 'pending' AND destination LIKE 'telegram:%' ORDER BY created_at, id LIMIT 1"
                ).fetchone()
                if row is None:
                    return None
                claimed = connection.execute(
                    "UPDATE outbox SET state = 'sending', attempts = attempts + 1 WHERE id = ? AND state = 'pending'",
                    (row["id"],),
                ).rowcount
                if claimed != 1:
                    return None
                return row["id"], row["destination"], json.loads(row["payload_json"])

    def _fail(self, outbox_id: str, reason: str) -> DeliveryResult:
        with self.database.connect() as connection:
            with self.database.transaction(connection):
                connection.execute(
                    "UPDATE outbox SET state = 'failed', last_error = ? WHERE id = ? AND state = 'sending'",
                    (reason, outbox_id),
                )
                AuditLog.append_in_transaction(
                    connection,
                    AuditEvent(
                        actor="system:telegram",
                        client="telegram",
                        tool="telegram_send",
                        outcome="failed",
                        result={"outbox_id": outbox_id, "reason": reason},
                        correlation_id=outbox_id,
                    ),
                )
        return DeliveryResult(outbox_id=outbox_id, state="failed", error=reason)
