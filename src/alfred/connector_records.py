"""Mutable current-state projections backed by immutable connector event history."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any


class ConnectorRecordStore:
    """Replace a connector's complete record-type snapshot in one transaction."""

    @staticmethod
    def replace_snapshot(
        connection: sqlite3.Connection,
        *,
        connector: str,
        account: str,
        record_type: str,
        records: dict[str, dict[str, Any]],
    ) -> None:
        observed_at = datetime.now(UTC).isoformat()
        connection.execute(
            "UPDATE connector_records SET active = 0, observed_at = ? WHERE connector = ? AND account = ? AND record_type = ?",
            (observed_at, connector, account, record_type),
        )
        for record_id, payload in records.items():
            connection.execute(
                """
                INSERT INTO connector_records (connector, account, record_type, record_id, payload_json, observed_at, active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(connector, account, record_type, record_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    observed_at = excluded.observed_at,
                    active = 1
                """,
                (connector, account, record_type, record_id, json.dumps(payload, sort_keys=True), observed_at),
            )
