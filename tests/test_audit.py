import sqlite3
from pathlib import Path

import pytest

from alfred.audit import AuditEvent, AuditLog
from alfred.db import Database


def test_audit_log_is_hash_chained_and_append_only(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    audit = AuditLog(database)

    first_id = audit.append(AuditEvent(actor="nico", tool="task_upsert", outcome="ok"))
    second_id = audit.append(AuditEvent(actor="nico", tool="brief_get", outcome="ok"))

    assert first_id != second_id
    assert audit.verify() is True

    with database.connect() as connection:
        first = connection.execute("SELECT * FROM tool_runs WHERE id = ?", (first_id,)).fetchone()
        second = connection.execute("SELECT * FROM tool_runs WHERE id = ?", (second_id,)).fetchone()
        assert second["previous_hash"] == first["record_hash"]
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("DELETE FROM tool_runs WHERE id = ?", (first_id,))


def test_audit_verification_detects_tampering(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    audit = AuditLog(database)
    record_id = audit.append(AuditEvent(actor="nico", tool="task_upsert", outcome="ok"))

    with database.connect() as connection:
        connection.execute("DROP TRIGGER tool_runs_prevent_update")
        connection.execute("UPDATE tool_runs SET outcome = 'tampered' WHERE id = ?", (record_id,))

    assert audit.verify() is False
