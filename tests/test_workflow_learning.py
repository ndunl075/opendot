import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alfred.db import Database
from alfred.mcp_server import create_server
from alfred.policy import PolicyError, PolicyStore
from alfred.workflow_learning import (
    WORKFLOW_TURN_ID_ENV,
    WorkflowLearningService,
    WorkflowObservationStore,
)


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
ACTOR = "owner:workflow-learning"


def _turn(
    store: WorkflowObservationStore,
    turn_id: str,
    observed_at: datetime,
    *,
    outcome: str = "ok",
    unsafe: bool = False,
) -> None:
    store.record_tool_call(
        turn_id,
        "connector_records_get",
        {
            "connector": "gmail",
            "record_type": "message",
            "limit": 20,
            "private_query": "nico@example.com Project Zephyr 2026-08-20",
        },
        observed_at=observed_at,
    )
    if unsafe:
        store.record_tool_call(
            turn_id,
            "action_commit",
            {"approval_id": "private-approval", "token": "alf_super_secret"},
            observed_at=observed_at + timedelta(seconds=1),
        )
    store.record_tool_call(
        turn_id,
        "message_draft",
        {
            "to": "nico@example.com",
            "subject": "Project Zephyr",
            "body": "Meet on 2026-08-20 about the secret acquisition",
        },
        observed_at=observed_at + timedelta(seconds=2),
    )
    store.complete_turn(turn_id, outcome=outcome, completed_at=observed_at + timedelta(seconds=3))


def _eligible_database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "alfred.db")
    store = WorkflowObservationStore(database)
    _turn(store, "one", NOW - timedelta(days=3))
    _turn(store, "two", NOW - timedelta(days=2))
    _turn(store, "three", NOW - timedelta(days=2, hours=-1))
    return database


def test_observations_store_structure_but_never_content(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    store = WorkflowObservationStore(database)

    _turn(store, "private", NOW - timedelta(days=1))

    with database.connect() as connection:
        payloads = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT arguments_json FROM workflow_tool_observations ORDER BY step_index"
            )
        ]
    serialized = json.dumps(payloads)
    assert payloads[0] == {
        "argument_keys": ["connector", "limit", "private_query", "record_type"],
        "literals": {"connector": "gmail", "record_type": "message"},
    }
    assert payloads[1]["literals"] == {}
    for private_value in (
        "nico@example.com",
        "Project Zephyr",
        "2026-08-20",
        "secret acquisition",
    ):
        assert private_value not in serialized


def test_mcp_records_only_a_successful_correlated_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "alfred.db"
    PolicyStore(Database(database_path)).grant(
        client_id="hermes",
        allowed_sensitivities={"personal"},
        allowed_tools={"connector_records_get"},
        allow_write=False,
    )
    monkeypatch.setenv(WORKFLOW_TURN_ID_ENV, "telegram:123")
    server = create_server(database_path, client_id="hermes")

    asyncio.run(
        server.call_tool(
            "connector_records_get",
            {"connector": "gmail", "record_type": "message", "limit": 5},
        )
    )

    with Database(database_path).connect() as connection:
        turn = connection.execute(
            "SELECT state FROM workflow_turns WHERE turn_id = 'telegram:123'"
        ).fetchone()
        observation = connection.execute(
            "SELECT tool_name, arguments_json FROM workflow_tool_observations"
        ).fetchone()
    assert turn["state"] == "pending"
    assert observation["tool_name"] == "connector_records_get"
    assert json.loads(observation["arguments_json"]) == {
        "argument_keys": ["connector", "limit", "record_type"],
        "literals": {"connector": "gmail", "record_type": "message"},
    }


def test_completing_a_turn_without_tools_does_not_create_evidence(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    database.migrate()
    WorkflowObservationStore(database).complete_turn("casual-chat", outcome="ok")

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_turns").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("times", "outcome"),
    [
        ([NOW - timedelta(days=2), NOW - timedelta(days=1)], "ok"),
        ([NOW - timedelta(hours=3), NOW - timedelta(hours=2), NOW - timedelta(hours=1)], "ok"),
        ([NOW - timedelta(days=3), NOW - timedelta(days=2), NOW - timedelta(days=1)], "error"),
    ],
)
def test_detector_requires_three_successes_across_two_days(
    tmp_path: Path, times: list[datetime], outcome: str
) -> None:
    database = Database(tmp_path / "alfred.db")
    store = WorkflowObservationStore(database)
    for index, observed_at in enumerate(times):
        _turn(store, str(index), observed_at, outcome=outcome)

    result = WorkflowLearningService(database, now=lambda: NOW).scan(actor=ACTOR)

    assert result.eligible_patterns == 0
    assert result.proposed == []


def test_repeated_successes_create_one_inert_versioned_diff(tmp_path: Path) -> None:
    database = _eligible_database(tmp_path)
    learner = WorkflowLearningService(database, now=lambda: NOW)

    first = learner.scan(actor=ACTOR)
    second = learner.scan(actor=ACTOR)

    assert first.eligible_patterns == 1
    assert len(first.proposed) == 1
    assert second.proposed == []
    proposal = first.proposed[0]
    assert proposal.state == "pending"
    assert proposal.version == 1
    assert proposal.occurrence_count == 3
    assert proposal.distinct_days == 2
    assert proposal.approval_id
    assert proposal.diff_text.startswith("--- /dev/null\n+++ ")
    assert "human-approval-required" in proposal.skill_markdown
    assert "Resolve from the current request only" in proposal.skill_markdown
    assert "`action_commit`" in proposal.skill_markdown
    assert "Call `action_commit`" not in proposal.skill_markdown
    for private_value in (
        "nico@example.com",
        "Project Zephyr",
        "2026-08-20",
        "secret acquisition",
    ):
        assert private_value not in proposal.skill_markdown
        assert private_value not in proposal.diff_text
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflow_skill_versions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1


def test_turn_with_consequential_commit_is_never_learning_evidence(tmp_path: Path) -> None:
    database = Database(tmp_path / "alfred.db")
    store = WorkflowObservationStore(database)
    for index in range(3):
        _turn(store, str(index), NOW - timedelta(days=3 - index), unsafe=True)

    result = WorkflowLearningService(database, now=lambda: NOW).scan(actor=ACTOR)

    assert result.eligible_patterns == 0
    assert result.proposed == []


def test_rejection_requires_original_actor_and_more_evidence_before_retry(tmp_path: Path) -> None:
    database = _eligible_database(tmp_path)
    learner = WorkflowLearningService(database, now=lambda: NOW)
    proposal = learner.scan(actor=ACTOR).proposed[0]

    with pytest.raises(PolicyError, match="requesting actor"):
        learner.reject(proposal.id, actor="someone-else")
    rejected = learner.reject(proposal.id, actor=ACTOR)
    assert rejected.state == "rejected"
    assert learner.scan(actor=ACTOR).proposed == []

    store = WorkflowObservationStore(database)
    _turn(store, "four", NOW - timedelta(days=1))
    _turn(store, "five", NOW - timedelta(hours=2))
    retried = learner.scan(actor=ACTOR).proposed

    assert len(retried) == 1
    assert retried[0].version == 2
    assert retried[0].occurrence_count == 5
    assert "@1" in retried[0].diff_text
    assert "@2" in retried[0].diff_text


def test_accepting_a_diff_is_audited_but_cannot_activate_it(tmp_path: Path) -> None:
    database = _eligible_database(tmp_path)
    learner = WorkflowLearningService(database, now=lambda: NOW)
    proposal = learner.scan(actor=ACTOR).proposed[0]

    with pytest.raises(PolicyError, match="requesting actor"):
        learner.accept(proposal.id, actor="someone-else")
    accepted = learner.accept(proposal.id, actor=ACTOR)

    assert accepted.state == "accepted"
    assert accepted.activated_at is None
    assert accepted.activated_path is None
    assert not hasattr(learner, "activate")
    with database.connect() as connection:
        approval_state = connection.execute(
            "SELECT state FROM approvals WHERE id = ?", (accepted.approval_id,)
        ).fetchone()[0]
        audit_tools = {
            row[0]
            for row in connection.execute(
                "SELECT tool FROM tool_runs WHERE tool LIKE 'workflow_skill_%'"
            )
        }
    assert approval_state == "approved"
    assert "workflow_skill_accept" in audit_tools
    assert learner.scan(actor=ACTOR).proposed == []
