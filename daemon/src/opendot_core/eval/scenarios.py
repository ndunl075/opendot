"""Scenarios S1 to S10 of ARCHITECTURE.md section 14 (M2), run against the real stack.

Each scenario builds its own stack over a scripted provider and asserts real
behavior: rows in the database, calls that reached (or did not reach) the fake
outside services, provider call counts and the requests the provider received.
The table in section 14 is the spec; do not weaken or reinterpret it here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..providers.errors import IncompleteResponse, UsageLimitExceeded
from ..providers.types import ChatRequest, Usage
from .fake_provider import (
    ScriptedProvider,
    SimulatedCrash,
    Turn,
    crash_turn,
    error_turn,
    text_turn,
    tool_turn,
)
from .harness import Stack, StackUnavailable, build_stack, load_attr, make_budgets

#: A reply that costs far more than the tiny budgets used below on a cheap-tier model.
BIG_USAGE = Usage(input_tokens=2000, output_tokens=500)


@dataclass(frozen=True)
class ScenarioResult:
    id: str
    title: str
    passed: bool
    detail: str = ""


class ScenarioFailure(AssertionError):
    """A scenario assertion did not hold."""


def check(condition: object, message: str) -> None:
    if not condition:
        raise ScenarioFailure(message)


ScenarioFn = Callable[[Path], None]


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    fn: ScenarioFn

    def run(self, tmp_dir: Path) -> ScenarioResult:
        try:
            self.fn(tmp_dir)
        except ScenarioFailure as failure:
            return ScenarioResult(self.id, self.title, False, str(failure))
        except AssertionError as failure:
            return ScenarioResult(self.id, self.title, False, f"assertion failed: {failure}")
        except StackUnavailable:
            raise
        except Exception as error:
            return ScenarioResult(self.id, self.title, False, f"{type(error).__name__}: {error}")
        return ScenarioResult(self.id, self.title, True, "")


SCENARIOS: dict[str, Scenario] = {}


def scenario(scenario_id: str, title: str) -> Callable[[ScenarioFn], ScenarioFn]:
    def register(fn: ScenarioFn) -> ScenarioFn:
        SCENARIOS[scenario_id] = Scenario(scenario_id, title, fn)
        return fn

    return register


def _state(stack: Stack, task_id: str) -> str:
    return str(stack.loop.task(task_id).state.value)


def _events(events: list[Any], kind: str) -> list[Any]:
    return [event for event in events if getattr(event, "type", None) == kind]


def _expect_state(stack: Stack, task_id: str, expected: str) -> None:
    actual = _state(stack, task_id)
    check(actual == expected, f"task state is {actual!r}, expected {expected!r}")


# S1 ---------------------------------------------------------------------------------------------


@scenario("S1", "A reminder is created and delivered on time")
def s1_reminder(tmp_dir: Path) -> None:
    stack = build_stack(
        tmp_dir,
        script=[
            tool_turn("reminder_set", {"text": "call mom", "minutes": 10}),
            text_turn("Done, I will remind you in 10 minutes."),
        ],
    )
    task_id, events = stack.start_and_run("remind me to call mom in 10 minutes")
    _expect_state(stack, task_id, "completed")
    created = stack.world.executed("reminder_set")
    check(len(created) == 1, f"reminder_set ran {len(created)} times, expected 1")

    with stack.database.connect() as connection:
        jobs = connection.execute("SELECT kind, state, next_run_at FROM jobs WHERE kind = 'reminder'").fetchall()
    check(len(jobs) == 1 and jobs[0]["state"] == "active", f"expected one active reminder job, found {len(jobs)}")
    due_at = datetime.fromisoformat(jobs[0]["next_run_at"])
    check((due_at - stack.clock()).total_seconds() == 600, "reminder is not due exactly 10 minutes from now")

    check(stack.run_due_jobs() == [], "reminder was delivered before it was due")
    stack.advance_time(minutes=5)
    check(stack.run_due_jobs() == [], "reminder was delivered 5 minutes early")
    stack.advance_time(minutes=5)
    delivered = stack.run_due_jobs()
    check(len(delivered) == 1, f"expected exactly one delivery at the due time, got {len(delivered)}")
    check(not delivered[0].late, "reminder was delivered late")
    with stack.database.connect() as connection:
        rows = connection.execute("SELECT destination, payload_json, state FROM outbox").fetchall()
    check(len(rows) == 1, f"expected one outbox message, found {len(rows)}")
    check("call mom" in json.loads(rows[0]["payload_json"])["text"], "outbox message does not carry the reminder text")
    check(rows[0]["destination"] == "desktop:owner", f"unexpected destination {rows[0]['destination']!r}")
    stack.advance_time(minutes=30)
    check(stack.run_due_jobs() == [], "reminder was delivered twice")


# S2 ---------------------------------------------------------------------------------------------

_DRAFT = {"to": "bob@example.com", "subject": "Lunch", "body": "Are you free Friday?"}


def _start_draft_task(stack: Stack) -> tuple[str, str]:
    """Run the draft request until it waits; return (task_id, approval_id)."""
    task_id, events = stack.start_and_run("draft an email to bob@example.com asking if he is free for lunch Friday")
    approvals = _events(events, "approval_required")
    check(len(approvals) == 1, f"expected one approval_required event, got {len(approvals)}")
    return task_id, approvals[0].approval_id


@scenario("S2", "Creating a Gmail draft produces an approval and does nothing without it")
def s2_draft_needs_approval(tmp_dir: Path) -> None:
    stack = build_stack(
        tmp_dir,
        script=[tool_turn("gmail_draft_create", _DRAFT), text_turn("Your draft is ready in Gmail.")],
    )
    task_id, approval_id = _start_draft_task(stack)
    _expect_state(stack, task_id, "waiting_approval")
    pending = stack.approvals.list_pending()
    check([a.id for a in pending] == [approval_id], "the approval is not the one pending approval")
    check(pending[0].action_type == "gmail_draft_create", f"wrong action type {pending[0].action_type!r}")
    check(pending[0].preview.get("to") == _DRAFT["to"], "the approval preview does not show the recipient")
    check(stack.world.gmail.create_calls == [], "a draft reached Gmail before approval")
    check(stack.world.executed("gmail_draft_create") == [], "the draft tool ran before approval")
    check(stack.provider.calls == 1, "the model was called again while the task waits for approval")

    # Running the task again without approving must still do nothing.
    stack.run(task_id)
    check(stack.world.gmail.create_calls == [], "a draft reached Gmail without approval after a re-run")
    _expect_state(stack, task_id, "waiting_approval")

    stack.approve(approval_id)
    stack.run(task_id)
    _expect_state(stack, task_id, "completed")
    check(len(stack.world.gmail.create_calls) == 1, f"draft created {len(stack.world.gmail.create_calls)} times")
    check(stack.world.gmail.create_calls[0]["to"] == _DRAFT["to"], "the draft went to the wrong recipient")


# S3 ---------------------------------------------------------------------------------------------


def _insert_rule_row(stack: Stack, values: dict[str, Any]) -> None:
    """Insert a rules row straight into SQLite, bypassing every check in RuleEngine."""
    with stack.database.connect() as connection:
        columns = connection.execute("PRAGMA table_info(rules)").fetchall()
        check(bool(columns), "the rules table does not exist")
        row: dict[str, Any] = {}
        for column in columns:
            name = column["name"]
            if name in values:
                row[name] = values[name]
            elif column["notnull"] and column["dflt_value"] is None:
                row[name] = ""
        names = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with stack.database.transaction(connection):
            connection.execute(f"INSERT INTO rules ({names}) VALUES ({marks})", list(row.values()))


@scenario("S3", "Deny-list actions are refused even with an auto rule")
def s3_deny_list(tmp_dir: Path) -> None:
    stack = build_stack(
        tmp_dir,
        script=[
            tool_turn("gmail_delete_message", {"message_id": "m-42"}),
            text_turn("I can't delete mail for you; please delete it in Gmail yourself."),
        ],
    )
    Behavior = load_attr("opendot_core.rules", "Behavior")
    Rule = load_attr("opendot_core.rules", "Rule")
    now = stack.clock().isoformat()
    auto_rule = Rule(
        id="s3-auto-delete",
        tool="gmail",
        action="delete",
        target="*",
        max_sensitivity="personal",
        behavior=Behavior.AUTO,
        created_by="user",
        created_at=now,
        note="S3 attempt to allow deleting mail",
    )
    try:
        stack.rules.add_rule(auto_rule)
    except Exception:
        pass
    else:
        raise ScenarioFailure("adding an auto rule for a deny-list action was accepted")

    _insert_rule_row(
        stack,
        {
            "id": "s3-direct",
            "tool": "gmail",
            "action": "delete",
            "target": "*",
            "max_sensitivity": "personal",
            "behavior": "auto",
            "created_by": "user",
            "created_at": now,
            "note": "S3 directly inserted",
        },
    )
    decision = stack.rules.decide(stack.tools.intent("gmail_delete_message", {"message_id": "m-42"}))
    check(str(decision.behavior) == "handoff", f"a directly inserted auto rule changed the decision to {decision.behavior}")
    check(decision.locked, "the deny-list decision is not marked locked")

    task_id, events = stack.start_and_run("delete the newsletter email m-42 from my gmail")
    check(stack.world.gmail.deleted == [], "a deny-list action deleted mail")
    check(stack.world.executed("gmail_delete_message") == [], "the deny-list tool ran")
    tool_events = _events(events, "tool")
    check(
        bool(tool_events) and all(event.behavior == "handoff" for event in tool_events),
        "the deny-list call was not decided as handoff",
    )
    check(all(not event.ok for event in tool_events), "the refused call was reported as ok")
    _expect_state(stack, task_id, "handed_off")


# S4 ---------------------------------------------------------------------------------------------


@scenario("S4", "Hitting the task budget pauses the task")
def s4_task_budget(tmp_dir: Path) -> None:
    stack = build_stack(
        tmp_dir,
        budgets=make_budgets(task_credits=0.01, daily_credits=1000.0),
        script=[
            tool_turn("memory_search", {"query": "food"}, usage=BIG_USAGE),
            text_turn("You like short answers."),
        ],
    )
    task_id, events = stack.start_and_run("what do you know about my preferences")
    _expect_state(stack, task_id, "paused_task_budget")
    check(stack.provider.calls == 1, f"{stack.provider.calls} model calls, expected 1 before the pause")
    check(bool(_events(events, "paused")), "no paused event was sent to the user")
    check(stack.loop.task(task_id).credits > 0.01, "the task was charged nothing for a large call")

    stack.run(task_id)
    _expect_state(stack, task_id, "paused_task_budget")
    check(stack.provider.calls == 1, "a paused task called the model again without the user's OK")

    stack.loop.continue_anyway(task_id)
    stack.run(task_id)
    _expect_state(stack, task_id, "completed")
    check(stack.provider.calls == 2, "the task did not continue after continue_anyway")
    check(len(stack.world.executed("memory_search")) == 1, "the finished tool call was repeated")


# S5 ---------------------------------------------------------------------------------------------


@scenario("S5", "Hitting the daily budget pauses all plan requests")
def s5_daily_budget(tmp_dir: Path) -> None:
    stack = build_stack(
        tmp_dir,
        budgets=make_budgets(task_credits=1000.0, daily_credits=0.01),
        script=[
            tool_turn("memory_search", {"query": "food"}, usage=BIG_USAGE),
            text_turn("Second task answer."),
        ],
    )
    first, _ = stack.start_and_run("what do you know about my preferences")
    _expect_state(stack, first, "paused_daily_budget")
    check(stack.provider.calls == 1, f"{stack.provider.calls} model calls, expected 1 before the pause")

    second, events = stack.start_and_run("what else do you know about me")
    check(stack.provider.calls == 1, "a second task reached the provider after the daily budget was hit")
    _expect_state(stack, second, "paused_daily_budget")
    check(bool(_events(events, "paused")), "the second task was not reported as paused")

    stack.advance_time(days=1)
    stack.run(second)
    _expect_state(stack, second, "completed")
    check(stack.provider.calls == 2, "the paused task did not run once the next day began")


# S6 ---------------------------------------------------------------------------------------------


@scenario("S6", "A 429 pauses all plan requests and never switches provider")
def s6_limit_no_switch(tmp_dir: Path) -> None:
    optin = ScriptedProvider([text_turn("paid answer")], name="openai_key", paid=True)
    stack = build_stack(
        tmp_dir,
        extra_providers=[optin],
        script=[error_turn(UsageLimitExceeded("429")), text_turn("Back again.")],
    )
    first, events = stack.start_and_run("what do you know about me")
    _expect_state(stack, first, "paused_plan_limit")
    check(bool(_events(events, "paused")), "the 429 was not reported as a pause")
    check(stack.provider.calls == 1, "the plan provider was called more than once")
    check(optin.calls == 0, "a 429 switched to another provider")

    second, _ = stack.start_and_run("what else do you know about me")
    _expect_state(stack, second, "paused_plan_limit")
    check(stack.provider.calls == 1, "a second task sent a plan request while paused")
    check(optin.calls == 0, "the second task used another provider")

    stack.loop.resume_after_plan_limit()
    stack.run(first)
    _expect_state(stack, first, "completed")
    check(stack.provider.calls == 2, "the task did not resume after the limit was cleared")
    check(optin.calls == 0, "resuming used another provider")


# S7 ---------------------------------------------------------------------------------------------


def _family(stack: Stack, model: str) -> str:
    for info in stack.catalog:
        if info.id == model:
            return info.family or ""
    return ""


@scenario("S7", "Escalation moves at most one tier per step and the top tier waits for approval")
def s7_escalation(tmp_dir: Path) -> None:
    stack = build_stack(
        tmp_dir / "a",
        script=[error_turn(IncompleteResponse()), error_turn(IncompleteResponse()), text_turn("Solved at the top.")],
    )
    task_id, events = stack.start_and_run("what do you know about me")
    families = [_family(stack, request.model) for request in stack.provider.requests]
    check(families == ["luna", "terra"], f"model tiers used before the top-tier pause: {families}")
    _expect_state(stack, task_id, "waiting_top_tier")
    check(stack.provider.calls == 2, "the top tier was called without the user's approval")
    check(bool(_events(events, "paused")), "the top-tier wait was not reported to the user")

    stack.run(task_id)
    check(stack.provider.calls == 2, "re-running a task waiting for the top tier called the model")

    stack.loop.approve_top_tier(task_id)
    stack.run(task_id)
    families = [_family(stack, request.model) for request in stack.provider.requests]
    check(families == ["luna", "terra", "sol"], f"model tiers after approval: {families}")
    _expect_state(stack, task_id, "completed")

    # If the top tier fails too, the task goes to the user with what was tried.
    failing = build_stack(
        tmp_dir / "b",
        script=[error_turn(IncompleteResponse()) for _ in range(3)] + [text_turn("never reached")],
    )
    failing_id, _ = failing.start_and_run("what do you know about me")
    failing.loop.approve_top_tier(failing_id)
    failing.run(failing_id)
    _expect_state(failing, failing_id, "handed_off")
    check(failing.provider.calls == 3, f"{failing.provider.calls} model calls, expected exactly 3 (one per tier)")


# S8 ---------------------------------------------------------------------------------------------


@scenario("S8", "A task resumes after a daemon restart")
def s8_resume(tmp_dir: Path) -> None:
    stack = build_stack(
        tmp_dir,
        script=[
            tool_turn("memory_search", {"query": "food"}),
            crash_turn(),
            text_turn("You prefer short answers."),
        ],
    )
    task_id = stack.loop.start_task("what do you know about my preferences")
    try:
        stack.run(task_id)
    except SimulatedCrash:
        pass
    else:
        raise ScenarioFailure("the simulated crash did not interrupt the task")
    check(len(stack.world.executed("memory_search")) == 1, "the tool did not run before the crash")

    restarted = stack.restart()
    check(restarted.loop is not stack.loop, "restart reused the old loop")
    check(_state(restarted, task_id) == "running", f"task state after the crash is {_state(restarted, task_id)!r}")
    resumed = restarted.loop.resume_all()
    check(task_id in resumed, "resume_all did not pick up the unfinished task")
    if _state(restarted, task_id) != "completed":
        restarted.run(task_id)
    _expect_state(restarted, task_id, "completed")
    check(len(restarted.world.executed("memory_search")) == 1, "a finished tool call was repeated after the restart")
    check(restarted.provider.calls == 3, f"{restarted.provider.calls} model calls, expected 3")
    check(restarted.loop.resume_all() == [], "resume_all found work again for a completed task")


# S9 ---------------------------------------------------------------------------------------------


@scenario("S9", "A replayed approval does not repeat the action")
def s9_replayed_approval(tmp_dir: Path) -> None:
    from ..policy import PolicyError

    stack = build_stack(
        tmp_dir,
        script=[tool_turn("gmail_draft_create", _DRAFT), text_turn("Your draft is ready.")],
    )
    task_id, approval_id = _start_draft_task(stack)
    stack.approve(approval_id)
    stack.run(task_id)
    _expect_state(stack, task_id, "completed")
    gmail = stack.world.gmail
    check(len(gmail.create_calls) == 1, f"draft created {len(gmail.create_calls)} times, expected 1")

    try:
        stack.approve(approval_id)
    except PolicyError:
        pass
    else:
        raise ScenarioFailure("an already-used approval was approved again")

    replay = json.loads(stack.tools.run_approved(approval_id))
    check(replay["replayed"] is True, "executing a used approval again was not a receipt replay")
    check(len(gmail.create_calls) == 1, "replaying the approval created a second draft")

    stack.run(task_id)
    check(len(gmail.create_calls) == 1, "re-running the finished task created a second draft")

    restarted = stack.restart()
    again = json.loads(restarted.tools.run_approved(approval_id))
    check(again["replayed"] is True, "after a restart the approval was not a receipt replay")
    check(len(gmail.create_calls) == 1, "a replay after a restart created a second draft")
    check(len(gmail.send_calls) == 0, "an email was sent")


# S10 --------------------------------------------------------------------------------------------


def assert_requests_prefix_stable(requests: list[ChatRequest]) -> None:
    """Byte-compare the stable prefix (instructions and tool schemas) of every request."""
    load_attr("opendot_core.agent.packer", "assert_prefix_stable")(requests)


@scenario("S10", "The stable prompt prefix is byte-identical across all steps of one task")
def s10_prefix_stable(tmp_dir: Path) -> None:
    stack = build_stack(
        tmp_dir,
        script=[
            tool_turn("memory_search", {"query": "food"}, cached_input_tokens=0),
            tool_turn("memory_search", {"query": "music"}, cached_input_tokens=50),
            text_turn("You like short answers.", cached_input_tokens=100),
        ],
    )
    task_id, _ = stack.start_and_run("what do you know about my preferences")
    _expect_state(stack, task_id, "completed")
    requests = stack.provider.requests
    check(len(requests) == 3, f"{len(requests)} model calls, expected 3 steps")

    first = requests[0]
    check(first.instructions != "", "the request has no instructions (no stable prefix)")
    check(bool(first.tools), "the request offers no tools")
    check(len(first.tools) <= 8, f"the tool group has {len(first.tools)} tools, more than 8")
    names = [tool.name for tool in first.tools]
    check(names == sorted(names), "the tool schemas are not sorted by name")
    for index, request in enumerate(requests[1:], start=2):
        check(
            request.instructions.encode() == first.instructions.encode(),
            f"instructions changed at step {index}",
        )
        check(
            [tool.model_dump_json() for tool in request.tools] == [tool.model_dump_json() for tool in first.tools],
            f"the tool group changed at step {index}",
        )
    assert_requests_prefix_stable(requests)

    record = stack.loop.task(task_id)
    check(sorted(record.tool_group) == sorted(names), "the recorded tool group differs from the tools sent")
    check(bool(record.prefix_hash), "the task has no recorded prefix hash")


__all__ = ["SCENARIOS", "Scenario", "ScenarioResult", "Turn"]
