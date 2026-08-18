"""Explain why Alfred did not answer the last Telegram message.

Read-only. Prints no message text and no destinations -- only states, counts,
and the first characters of your own messages, so the output is safe to paste.

    python scripts/why_silent.py [path-to-alfred.db]
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    default = os.environ.get("ALFRED_DB_PATH") or ".alfred/alfred.db"
    path = Path(argv[1] if len(argv) > 1 else default)
    if not path.exists():
        print(f"no database at {path}; pass the path or set ALFRED_DB_PATH")
        return 1

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    print(f"database: {path}")

    states = connection.execute(
        "SELECT state, COUNT(*) AS count FROM outbox GROUP BY state ORDER BY state"
    ).fetchall()
    print("\noutbox by state:")
    for row in states or []:
        print(f"  {row['state']:<8} {row['count']}")
    if not states:
        print("  (empty)")

    latest = connection.execute(
        """
        SELECT external_id, occurred_at, substr(COALESCE(content, ''), 1, 40) AS opening
        FROM events WHERE source = 'telegram'
        ORDER BY rowid DESC LIMIT 1
        """
    ).fetchone()
    if latest is None:
        print("\nno Telegram message was ever stored: intake never ran.")
        return 0

    print(
        f"\nnewest stored message: update {latest['external_id']} "
        f"at {latest['occurred_at']}\n  opens with: {latest['opening']!r}"
    )

    replies = connection.execute(
        """
        SELECT idempotency_key, state, attempts, COALESCE(last_error, '') AS last_error
        FROM outbox WHERE idempotency_key LIKE ?
        ORDER BY rowid
        """,
        (f"hermes-reply:{latest['external_id']}:%",),
    ).fetchall()

    if not replies:
        print(
            "\nVERDICT: no reply was ever queued for it. The message was received "
            "and stored, so intake is fine and the agent never produced an answer. "
            "Check that `alfred run` is up and was started with --hermes-profile."
        )
        return 0

    print("\nqueued reply bubbles:")
    for row in replies:
        index = row["idempotency_key"].rsplit(":", 1)[-1]
        error = f"  {row['last_error']}" if row["last_error"] else ""
        print(f"  bubble {index}: {row['state']} (attempts {row['attempts']}){error}")

    states_present = {str(row["state"]) for row in replies}
    print()
    if "sending" in states_present:
        print(
            "VERDICT: stranded claim. A bubble was claimed and never finished, and "
            "nothing retries a claimed row, so this message can never be answered. "
            "`UPDATE outbox SET state='pending' WHERE state='sending';` releases it, "
            "but a claimed row may already have reached Telegram, so that can "
            "re-send something you saw."
        )
    elif "failed" in states_present:
        print("VERDICT: the send itself failed. The reason is above; nothing auto-retries.")
    elif states_present == {"sent"}:
        print(
            "VERDICT: Alfred did send this reply. If it never showed up, the problem "
            "is between the Bot API and your client, not in Alfred."
        )
    else:
        print("VERDICT: the reply is still queued and undelivered; the outbox worker is not running.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
