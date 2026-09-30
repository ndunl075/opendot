from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from opendot_core.eval.fake_provider import text_turn
from opendot_core.eval.harness import FakeClock, StackUnavailable, build_world, load_attr
from opendot_core.policy import PolicyError

DRAFT = {"to": "bob@example.com", "subject": "Lunch", "body": "Friday?"}


def test_fake_clock_advances_and_is_injectable() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    clock.advance(minutes=90)
    assert clock() == datetime(2026, 1, 1, 1, 30, tzinfo=UTC)


def test_build_world_wires_database_provider_and_clock(tmp_path: Path) -> None:
    stack = build_world(tmp_path, script=[text_turn("hi")])
    assert stack.provider.name == "chatgpt_plan"
    assert stack.database.path == tmp_path / "opendot.db"
    assert [m.family for m in stack.catalog] == ["luna", "terra", "sol"]
    before = stack.clock()
    assert stack.advance_time(days=1) - before == (stack.clock() - before)
    assert (stack.clock() - before).days == 1


def test_reminder_tool_creates_a_real_job_that_the_job_runner_delivers_once(tmp_path: Path) -> None:
    stack = build_world(tmp_path)
    stack.tools.run("reminder_set", {"text": "call mom", "minutes": 10}, task_id="t1", call_id="c1")
    stack.tools.run("reminder_set", {"text": "call mom", "minutes": 10}, task_id="t1", call_id="c1")
    assert stack.run_due_jobs() == []
    stack.advance_time(minutes=10)
    delivered = stack.run_due_jobs()
    assert len(delivered) == 1 and not delivered[0].late
    with stack.database.connect() as connection:
        rows = connection.execute("SELECT payload_json FROM outbox").fetchall()
    assert "call mom" in json.loads(rows[0]["payload_json"])["text"]
    assert stack.run_due_jobs() == []


def test_draft_needs_approval_then_executes_once_and_replays(tmp_path: Path) -> None:
    stack = build_world(tmp_path)
    approval = stack.tools.propose("gmail_draft_create", DRAFT, task_id="t1")
    assert stack.world.gmail.create_calls == []
    with pytest.raises(PolicyError):
        stack.tools.run_approved(approval.id)
    assert stack.world.gmail.create_calls == []

    stack.approve(approval.id)
    first = json.loads(stack.tools.run_approved(approval.id))
    second = json.loads(stack.tools.run_approved(approval.id))
    assert (first["replayed"], second["replayed"]) == (False, True)
    assert len(stack.world.gmail.create_calls) == 1
    assert len(stack.world.executed("gmail_draft_create")) == 1
    with pytest.raises(PolicyError):
        stack.approve(approval.id)


def test_tool_specs_are_copies_and_named(tmp_path: Path) -> None:
    stack = build_world(tmp_path)
    names = [spec.name for spec in stack.tools.specs()]
    assert names == ["memory_search", "reminder_set", "gmail_draft_create", "gmail_delete_message"]
    stack.tools.specs()[0].name = "changed"
    assert stack.tools.specs()[0].name == "memory_search"


def test_load_attr_reports_a_missing_module() -> None:
    with pytest.raises(StackUnavailable) as error:
        load_attr("opendot_core.no_such_module", "Thing")
    assert error.value.module == "opendot_core.no_such_module"
