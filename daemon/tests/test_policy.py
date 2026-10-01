from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from opendot_core.audit import AuditLog
from opendot_core.db import Database
from opendot_core.memory_graph import MemoryGraph
from opendot_core.policy import ApprovalService, PolicyError, PolicyStore


def test_unregistered_client_is_default_deny_and_grants_are_explicit(tmp_path: Path) -> None:
    database = Database(tmp_path / "opendot.db")
    policy = PolicyStore(database)

    assert policy.scope_for("cursor").active is False
    with pytest.raises(PolicyError, match="not allowed"):
        policy.require_read("cursor", "memory_search")

    scope = policy.grant(
        client_id="cursor",
        allowed_sensitivities={"public", "personal"},
        allowed_tools={"memory_search"},
    )

    assert policy.require_read("cursor", "memory_search") == scope
    with pytest.raises(PolicyError, match="not allowed to write"):
        policy.require_write("cursor", "memory_search")


def test_scope_filters_sensitive_memory_before_retrieval(tmp_path: Path) -> None:
    database = Database(tmp_path / "opendot.db")
    graph = MemoryGraph(database)
    public = graph.remember("OpenDot project is local.", sensitivity="personal")
    secret = graph.remember("OpenDot project has private health notes.", sensitivity="sensitive")

    result = graph.search("OpenDot project", allowed_sensitivities={"public", "personal"})

    assert [memory.id for memory in result.memories] == [public.id]
    assert secret.id not in [memory.id for memory in result.memories]


def test_approval_is_actor_bound_expiring_hash_backed_and_single_use(tmp_path: Path) -> None:
    database = Database(tmp_path / "opendot.db")
    approvals = ApprovalService(database)
    now = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    proposed = approvals.propose(
        actor="sam",
        action_type="send_message",
        preview={"recipient": "friend", "text": "hello"},
        ttl=timedelta(minutes=5),
        now=now,
    )

    with pytest.raises(PolicyError, match="only the requesting"):
        approvals.approve(proposed.id, actor="other", now=now)
    issued = approvals.approve(proposed.id, actor="sam", now=now)
    assert issued.token.startswith("alf_")
    consumed = approvals.consume(issued.approval.id, actor="sam", token=issued.token, now=now)

    assert consumed.state == "consumed"
    with pytest.raises(PolicyError, match="not ready"):
        approvals.consume(issued.approval.id, actor="sam", token=issued.token, now=now)
    with database.connect() as connection:
        stored = connection.execute("SELECT token_hash FROM approvals WHERE id = ?", (proposed.id,)).fetchone()
        audit_json = connection.execute("SELECT arguments_json, result_json FROM tool_runs").fetchall()
    assert stored["token_hash"] != issued.token
    assert all(issued.token not in row["arguments_json"] + row["result_json"] for row in audit_json)
    assert AuditLog(database).verify() is True


def test_verify_checks_a_consumed_token_without_changing_state(tmp_path: Path) -> None:
    database = Database(tmp_path / "opendot.db")
    approvals = ApprovalService(database)
    now = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    proposed = approvals.propose(actor="sam", action_type="calendar_event_create", preview={}, now=now)
    issued = approvals.approve(proposed.id, actor="sam", now=now)
    approvals.consume(issued.approval.id, actor="sam", token=issued.token, now=now)

    verified = approvals.verify(issued.approval.id, actor="sam", token=issued.token)
    assert verified.state == "consumed"
    # A second verify still succeeds: unlike consume(), it never changes state.
    assert approvals.verify(issued.approval.id, actor="sam", token=issued.token).state == "consumed"

    with pytest.raises(PolicyError, match="does not match"):
        approvals.verify(issued.approval.id, actor="someone-else", token=issued.token)
    with pytest.raises(PolicyError, match="invalid"):
        approvals.verify(issued.approval.id, actor="sam", token="wrong-token")


def test_verify_rejects_a_never_approved_proposal(tmp_path: Path) -> None:
    approvals = ApprovalService(Database(tmp_path / "opendot.db"))
    proposed = approvals.propose(actor="sam", action_type="calendar_event_create", preview={})

    with pytest.raises(PolicyError, match="not usable: pending"):
        approvals.verify(proposed.id, actor="sam", token="anything")


def test_expired_approval_cannot_be_approved(tmp_path: Path) -> None:
    approvals = ApprovalService(Database(tmp_path / "opendot.db"))
    now = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    proposed = approvals.propose(actor="sam", action_type="send_message", preview={}, ttl=timedelta(minutes=1), now=now)

    with pytest.raises(PolicyError, match="expired"):
        approvals.approve(proposed.id, actor="sam", now=now + timedelta(minutes=2))
