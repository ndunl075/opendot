"""Shared health classification over every connector's sync_state row.

ARCHITECTURE.md's connector contract lists health() -> status as part of
every connector's interface, but each connector (canvas.py, github.py,
gmail.py, google_calendar.py) is an independent class with no shared base,
and each already writes its outcome to the same sync_state table using the
same success/error convention. Rather than add a redundant health() method
to every class that would just re-query that same table, classification
lives here once and is shared by the CLI and the MCP connector_status tool.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from .db import Database

HealthState = Literal["ok", "stale", "error", "never_synced"]


class ConnectorHealth(BaseModel):
    connector: str
    account: str
    state: HealthState
    last_success_at: datetime | None
    last_error: str | None


def connector_health(
    database: Database,
    *,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(hours=24),
) -> list[ConnectorHealth]:
    """Classify every connector/account pair without exposing credentials or synced content."""
    checked_at = (now or datetime.now(UTC)).astimezone(UTC)
    database.migrate()
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT connector, account, last_success_at, last_error FROM sync_state ORDER BY connector, account"
        ).fetchall()
    results: list[ConnectorHealth] = []
    for row in rows:
        last_success = datetime.fromisoformat(row["last_success_at"]) if row["last_success_at"] else None
        state: HealthState
        if row["last_error"]:
            # sync_state clears last_error on every successful sync, so a
            # non-null value here always reflects the most recent attempt.
            state = "error"
        elif last_success is None:
            state = "never_synced"
        elif checked_at - last_success > stale_after:
            state = "stale"
        else:
            state = "ok"
        results.append(
            ConnectorHealth(
                connector=row["connector"],
                account=row["account"],
                state=state,
                last_success_at=last_success,
                last_error=row["last_error"],
            )
        )
    return results
