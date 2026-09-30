"""Agent loop behavior beyond the S1-S10 scenarios (M2 task 2.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opendot_core.agent.loop import AgentLoop
from opendot_core.agent.loop_api import TaskState
from opendot_core.agent.packer import SUMMARY_MARKER, PromptPacker
from opendot_core.agent.tool_groups import MAX_TOOLS_PER_GROUP, choose_task_tool_group
from opendot_core.eval.fake_provider import error_turn, text_turn, tool_turn
from opendot_core.eval.harness import Stack, build_stack, make_budgets
from opendot_core.providers.errors import AuthRequired, RateLimited, UsageLimitExceeded
from opendot_core.providers.types import Usage

DRAFT = {"to": "bob@example.com", "subject": "Lunch", "body": "Free Friday?"}


def events_of(events: list, kind: str) -> list:
    return [event for event in events if event.type == kind]


def test_plain_answer_completes_in_one_call(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[text_turn("Hi Nico.")])
    task_id, events = stack.start_and_run("hello")
    done = events_of(events, "done")
    assert [e.text for e in events_of(events, "text")] == ["Hi Nico."]
    assert done and done[0].state is TaskState.COMPLETED and done[0].text == "Hi Nico."
    assert done[0].model == "fake-luna" and done[0].effort == "low" and done[0].credits > 0
    record = stack.loop.task(task_id)
    assert record.steps_completed == 1 and record.state is TaskState.COMPLETED


def test_first_request_carries_message_later_ones_append_history(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("memory_search", {"query": "x"}), text_turn("ok")])
    stack.start_and_run("what do you know")
    first, second = stack.provider.requests
    assert first.input[-1].role == "user" and "what do you know" in first.input[-1].content
    # History is append-only: the second request starts with every item the first one sent.
    assert [item.model_dump() for item in second.input[: len(first.input)]] == [
        item.model_dump() for item in first.input
    ]
    roles = [item.role for item in second.input]
    assert roles == ["user", "assistant", "tool", "user"]
    assert second.input[2].tool_call_id == second.input[1].tool_call_id


def test_unknown_task_job_type_is_rejected(tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    with pytest.raises(ValueError):
        stack.loop.start_task("hi", job_type="nonsense")


def test_start_task_calls_no_model(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[text_turn("x")])
    stack.loop.start_task("hello")
    assert stack.provider.calls == 0


def test_kill_switch_stops_model_requests_until_unpaused(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[text_turn("after unpause")])
    stack.loop.pause("user")
    assert stack.loop.paused() == "user"
    task_id, events = stack.start_and_run("hello")
    assert stack.loop.task(task_id).state is TaskState.PAUSED
    assert events_of(events, "paused") and stack.provider.calls == 0
    stack.loop.unpause()
    stack.run(task_id)
    assert stack.loop.task(task_id).state is TaskState.COMPLETED


def test_kill_switch_holds_approved_action(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("gmail_draft_create", DRAFT), text_turn("done")])
    task_id, events = stack.start_and_run("draft an email to bob@example.com about lunch")
    approval_id = events_of(events, "approval_required")[0].approval_id
    stack.approve(approval_id)
    stack.loop.pause("user")
    stack.run(task_id)
    assert stack.world.gmail.create_calls == []
    stack.loop.unpause()
    stack.run(task_id)
    assert len(stack.world.gmail.create_calls) == 1
    assert stack.loop.task(task_id).state is TaskState.COMPLETED


def test_rejected_approval_tells_the_model_and_runs_nothing(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("gmail_draft_create", DRAFT), text_turn("Okay, not drafting.")])
    task_id, events = stack.start_and_run("draft an email to bob@example.com about lunch")
    approval_id = events_of(events, "approval_required")[0].approval_id
    stack.approvals.reject(approval_id, actor=stack.tools.actor)
    events = stack.run(task_id)
    assert stack.world.gmail.create_calls == []
    assert stack.loop.task(task_id).state is TaskState.COMPLETED
    tool_result = stack.provider.requests[-1].input[-2]
    assert tool_result.role == "tool" and "did not approve" in tool_result.content
    assert [e.ok for e in events_of(events, "tool")] == [False]


def test_approval_event_carries_reviewer_note(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("gmail_draft_create", DRAFT)])
    task_id, events = stack.start_and_run("draft an email to bob@example.com about lunch")
    approval = events_of(events, "approval_required")[0]
    assert approval.action_type == "gmail_draft_create"
    assert approval.review_verdict == "concern" and "example.com" in approval.review
    assert stack.loop.task_for_approval(approval.approval_id) == task_id
    assert stack.loop.task_for_approval("nope") is None


def test_reviewer_block_creates_no_approval(tmp_path: Path) -> None:
    leaky = dict(DRAFT, body="my password: hunter2hunter2")
    stack = build_stack(tmp_path, script=[tool_turn("gmail_draft_create", leaky)])
    task_id, events = stack.start_and_run("draft an email to bob@example.com with my password")
    assert events_of(events, "approval_required") == []
    assert stack.approvals.list_pending() == []
    assert stack.loop.task(task_id).state is TaskState.HANDED_OFF


def test_preapproved_action_still_asks_without_a_preapproval_rule(tmp_path: Path) -> None:
    # auto_if_preapproved needs a rule; the default for drafts is ask, whatever the message says.
    stack = build_stack(tmp_path, script=[tool_turn("gmail_draft_create", DRAFT)])
    task_id = stack.loop.start_task(
        "draft an email to bob@example.com", preapproved_actions=frozenset({"gmail_draft_create"})
    )
    events = stack.run(task_id)
    assert events_of(events, "approval_required")
    assert stack.world.gmail.create_calls == []


def test_auto_if_preapproved_rule_runs_only_when_preapproved(tmp_path: Path) -> None:
    def stack_with_rule(path: Path) -> Stack:
        stack = build_stack(path, script=[tool_turn("reminder_set", {"text": "x", "minutes": 5}), text_turn("ok")])
        stack.rules.add_rule(
            tool="reminder_set", action="create", behavior="auto_if_preapproved", max_sensitivity="personal"
        )
        return stack

    asked = stack_with_rule(tmp_path / "a")
    _, events = asked.start_and_run("remind me")
    assert events_of(events, "tool")[0].behavior == "ask"
    assert asked.world.executed("reminder_set") == []

    allowed = stack_with_rule(tmp_path / "b")
    task_id = allowed.loop.start_task("remind me", preapproved_actions=frozenset({"reminder_set:create"}))
    events = allowed.run(task_id)
    assert events_of(events, "tool")[0].behavior == "auto"
    assert len(allowed.world.executed("reminder_set")) == 1


def test_rate_limit_429_also_pauses_plan_requests(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[error_turn(RateLimited("slow down")), text_turn("ok")])
    task_id, _ = stack.start_and_run("hello")
    assert stack.loop.task(task_id).state is TaskState.PAUSED_PLAN_LIMIT
    other, _ = stack.start_and_run("again")
    assert stack.loop.task(other).state is TaskState.PAUSED_PLAN_LIMIT and stack.provider.calls == 1


def test_plan_limit_pause_survives_restart(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[error_turn(UsageLimitExceeded("429")), text_turn("ok")])
    stack.start_and_run("hello")
    restarted = stack.restart()
    task_id, _ = restarted.start_and_run("again")
    assert restarted.loop.task(task_id).state is TaskState.PAUSED_PLAN_LIMIT
    assert restarted.provider.calls == 1


def test_auth_error_fails_without_escalating(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[error_turn(AuthRequired("expired"))])
    task_id, events = stack.start_and_run("hello")
    assert stack.loop.task(task_id).state is TaskState.FAILED
    assert stack.provider.calls == 1
    assert events_of(events, "done")[0].text == AuthRequired.user_message


def test_escalation_returns_to_cheap_tier_after_success(tmp_path: Path) -> None:
    from opendot_core.providers.errors import IncompleteResponse

    stack = build_stack(
        tmp_path,
        script=[error_turn(IncompleteResponse()), tool_turn("memory_search", {"query": "x"}), text_turn("ok")],
    )
    stack.start_and_run("what do you know")
    assert [r.model for r in stack.provider.requests] == ["fake-luna", "fake-terra", "fake-luna"]


def test_deep_job_waits_for_top_tier_before_any_call(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[text_turn("deep answer")])
    task_id, events = stack.start_and_run("think hard", job_type="deep")
    assert stack.loop.task(task_id).state is TaskState.WAITING_TOP_TIER and stack.provider.calls == 0
    stack.loop.approve_top_tier(task_id)
    stack.run(task_id)
    assert stack.provider.requests[0].model == "fake-sol" and stack.provider.requests[0].effort == "high"


def test_step_cap_hands_off(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("memory_search", {"query": str(n)}) for n in range(5)])
    stack.loop.max_model_steps = 3
    task_id, _ = stack.start_and_run("loop forever")
    assert stack.loop.task(task_id).state is TaskState.HANDED_OFF
    assert stack.provider.calls == 3


def test_tool_failure_goes_back_to_the_model(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("reminder_set", {"text": "x"}), text_turn("sorry")])
    task_id, events = stack.start_and_run("remind me")
    assert [e.ok for e in events_of(events, "tool")] == [False]
    assert "failed" in stack.provider.requests[-1].input[-2].content
    assert stack.loop.task(task_id).state is TaskState.COMPLETED


def test_unknown_tool_is_reported_not_run(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("format_disk", {}), text_turn("ok")])
    _, events = stack.start_and_run("hello")
    assert events_of(events, "tool")[0].behavior == "unknown" and not events_of(events, "tool")[0].ok


def test_anomaly_spike_pauses_everything(tmp_path: Path) -> None:
    big = Usage(input_tokens=2_000_000, output_tokens=0)  # 10 credits on luna
    stack = build_stack(
        tmp_path,
        budgets=make_budgets(task_credits=1000.0, daily_credits=15.0),
        script=[tool_turn("memory_search", {"query": "x"}, usage=big), text_turn("never")],
    )
    task_id, events = stack.start_and_run("hello")
    assert stack.loop.task(task_id).state is TaskState.PAUSED
    assert (stack.loop.paused() or "").startswith("anomaly:")
    assert stack.provider.calls == 1


def test_anomaly_many_actions_in_a_row(tmp_path: Path) -> None:
    turns = [tool_turn("memory_search", {"query": str(n)}) for n in range(4)] + [text_turn("done")]
    stack = build_stack(tmp_path, script=turns)
    stack.loop.anomaly.limits.max_actions = 3
    task_id, _ = stack.start_and_run("search a lot")
    assert stack.loop.task(task_id).state is TaskState.PAUSED
    assert len(stack.world.executed("memory_search")) == 3


def test_new_recipient_domain_pauses_an_auto_action(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("gmail_draft_create", DRAFT)])
    stack.rules.add_rule(tool="message_draft", target="bob@example.com", behavior="auto", max_sensitivity="personal")
    task_id, _ = stack.start_and_run("draft to bob@example.com")
    assert stack.loop.task(task_id).state is TaskState.PAUSED
    assert "new recipient domain" in (stack.loop.paused() or "")
    assert stack.world.gmail.create_calls == []


def test_compaction_summarizes_once_and_keeps_prefix(tmp_path: Path) -> None:
    long = "word " * 400
    stack = build_stack(
        tmp_path,
        script=[
            tool_turn("memory_search", {"query": long}),
            text_turn("SUMMARY-TEXT"),  # the compaction call, cheap tier
            text_turn("final"),
        ],
    )
    stack.loop.compaction_threshold = 500
    stack.loop.compaction_keep_recent = 2
    task_id, _ = stack.start_and_run("hello")
    assert stack.loop.task(task_id).state is TaskState.COMPLETED
    first, compaction, last = stack.provider.requests
    assert compaction.instructions.startswith("Summarize") and compaction.model == "fake-luna"
    assert "hello" in compaction.input[0].content
    assert last.instructions.endswith("SUMMARY-TEXT")
    assert last.instructions.split(SUMMARY_MARKER)[0] == first.instructions.split(SUMMARY_MARKER)[0]
    assert [item.role for item in last.input] == ["assistant", "tool", "user"]
    assert "summarize" in {line.key for line in stack.meter.summary().by_job_type}


def test_no_compaction_below_threshold(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("memory_search", {"query": "x"}), text_turn("final")])
    stack.start_and_run("hello")
    assert stack.provider.calls == 2


def test_concurrent_run_of_same_task_is_a_no_op(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[text_turn("x")])
    task_id = stack.loop.start_task("hello")
    outer = stack.loop.run(task_id)
    lock = stack.loop._lock(task_id)
    lock.acquire()
    try:
        assert list(outer) == []
    finally:
        lock.release()
    assert stack.provider.calls == 0


def test_tool_group_choice_is_capped_and_sorted() -> None:
    few = ["b", "a"]
    assert choose_task_tool_group("anything", few) == ["a", "b"]
    many = [f"tool_{n:02d}" for n in range(12)] + ["reminder_set", "memory_search"]
    group = choose_task_tool_group("remind me to stretch", many)
    assert len(group) == MAX_TOOLS_PER_GROUP and group == sorted(group)
    assert "reminder_set" in group
    assert choose_task_tool_group("remind me to stretch", many) == group


def test_loop_builds_with_defaults(tmp_path: Path) -> None:
    stack: Stack = build_stack(tmp_path)
    loop = AgentLoop(
        stack.database, stack.registry, stack.router, stack.meter, stack.rules, PromptPacker(), stack.tools
    )
    assert loop.paused() is None


def test_resume_after_plan_limit_also_resumes_the_provider(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[text_turn("x")])
    resumed: list[bool] = []
    stack.provider.resume = lambda: resumed.append(True)  # type: ignore[attr-defined]
    stack.loop.resume_after_plan_limit()
    assert resumed == [True]


# -- M2 review fixes (docs/reviews/m2.md) ------------------------------------------------------


def _lose_tokens(stack: Stack) -> None:
    """Drop the scenario's token store, as a daemon restart drops the in-memory escrow."""
    stack.world.tokens.clear()


def test_f3_resume_all_runs_tasks_whose_approval_was_decided(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("gmail_draft_create", DRAFT), text_turn("done")])
    task_id, events = stack.start_and_run("draft an email to bob@example.com about lunch")
    stack.approve(events_of(events, "approval_required")[0].approval_id)
    restarted = stack.restart()
    assert task_id in restarted.loop.resume_all()
    assert restarted.loop.task(task_id).state is TaskState.COMPLETED
    assert len(restarted.world.gmail.create_calls) == 1


def test_f3_lost_token_asks_again_and_does_nothing(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("gmail_draft_create", DRAFT), text_turn("done")])
    task_id, events = stack.start_and_run("draft an email to bob@example.com about lunch")
    first = events_of(events, "approval_required")[0].approval_id
    stack.approve(first)
    _lose_tokens(stack)
    restarted = stack.restart()
    restarted.loop.resume_all()
    assert restarted.loop.task(task_id).state is TaskState.WAITING_APPROVAL
    assert restarted.world.gmail.create_calls == []
    fresh = restarted.approvals.list_pending()
    assert len(fresh) == 1 and fresh[0].id != first
    assert restarted.loop.task_for_approval(fresh[0].id) == task_id
    restarted.approve(fresh[0].id)
    restarted.run(task_id)
    assert restarted.loop.task(task_id).state is TaskState.COMPLETED
    assert len(restarted.world.gmail.create_calls) == 1


def test_f3_consumed_approval_with_lost_token_hands_off(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("gmail_draft_create", DRAFT)])
    task_id, events = stack.start_and_run("draft an email to bob@example.com about lunch")
    approval_id = events_of(events, "approval_required")[0].approval_id
    stack.approve(approval_id)
    stack.approvals.consume(approval_id, actor=stack.tools.actor, token=stack.world.tokens[approval_id])
    _lose_tokens(stack)
    stack.run(task_id)
    assert stack.loop.task(task_id).state is TaskState.HANDED_OFF
    assert stack.world.gmail.create_calls == []


def test_f4_budget_is_checked_again_after_compaction(tmp_path: Path) -> None:
    long = "word " * 400
    stack = build_stack(
        tmp_path,
        budgets=make_budgets(task_credits=1000.0, daily_credits=0.02),
        script=[
            tool_turn("memory_search", {"query": long}, usage=Usage(input_tokens=1000)),  # 0.005 credits
            text_turn("SUMMARY", usage=Usage(input_tokens=3000)),  # 0.015: the daily budget is now reached
            text_turn("never"),
        ],
    )
    stack.loop.compaction_threshold = 500
    stack.loop.compaction_keep_recent = 2
    task_id, _ = stack.start_and_run("hello")
    assert stack.provider.calls == 2
    assert stack.loop.task(task_id).state is TaskState.PAUSED_DAILY_BUDGET


def _model_reviewed_stack(path: Path, script: list) -> Stack:
    from opendot_core.agent.reviewer import Reviewer

    stack = build_stack(path, script=script)
    stack.loop.reviewer = Reviewer(stack.database, stack.registry, stack.router, stack.meter, clock=stack.clock)
    return stack


def test_f5_reviewer_429_pauses_all_plan_requests_and_shows_no_card(tmp_path: Path) -> None:
    ok = json.dumps({"verdict": "ok", "reasons": []})
    stack = _model_reviewed_stack(
        tmp_path,
        [tool_turn("gmail_draft_create", DRAFT), error_turn(UsageLimitExceeded("429")), text_turn(ok)],
    )
    task_id, events = stack.start_and_run("draft an email to bob@example.com about lunch")
    assert stack.loop.task(task_id).state is TaskState.PAUSED_PLAN_LIMIT
    assert events_of(events, "approval_required") == [] and stack.approvals.list_pending() == []
    other, _ = stack.start_and_run("hello")
    assert stack.loop.task(other).state is TaskState.PAUSED_PLAN_LIMIT and stack.provider.calls == 2
    stack.loop.resume_after_plan_limit()
    events = stack.run(task_id)
    assert events_of(events, "approval_required") and len(stack.approvals.list_pending()) == 1


def test_f6_default_reviewer_runs_the_mid_tier_model_pass(tmp_path: Path) -> None:
    concern = json.dumps({"verdict": "concern", "reasons": ["hm"]})
    stack = build_stack(tmp_path, script=[tool_turn("gmail_draft_create", DRAFT), text_turn(concern)])
    loop = AgentLoop(
        stack.database, stack.registry, stack.router, stack.meter, stack.rules, PromptPacker(), stack.tools,
        clock=stack.clock,
    )
    task_id = loop.start_task("draft an email to bob@example.com about lunch")
    events = list(loop.run(task_id))
    assert stack.provider.requests[1].model == "fake-terra"
    assert "hm" in events_of(events, "approval_required")[0].review


def test_f8_restarted_tool_step_rechecks_the_rules(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("reminder_set", {"text": "x", "minutes": 5}), text_turn("ok")])
    task_id = stack.loop.start_task("remind me")
    real_run = stack.tools.run

    class Crash(BaseException):
        pass

    def crash(*args: object, **kwargs: object) -> str:
        raise Crash

    stack.tools.run = crash  # type: ignore[method-assign]
    with pytest.raises(Crash):
        stack.run(task_id)
    stack.tools.run = real_run  # type: ignore[method-assign]
    restarted = stack.restart()
    restarted.rules.add_rule(tool="reminder_set", action="create", behavior="handoff", max_sensitivity="personal")
    restarted.loop.resume_all()
    assert restarted.loop.task(task_id).state is TaskState.HANDED_OFF
    assert restarted.world.executed("reminder_set") == []


def test_f9_tool_outside_the_task_group_is_refused(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("memory_search", {"query": "x"}), text_turn("ok")])
    task_id = stack.loop.start_task("hello")
    with stack.database.connect() as connection:
        connection.execute("UPDATE agent_tasks SET tool_group_json = ? WHERE id = ?", (json.dumps(["reminder_set"]), task_id))
        connection.commit()
    events = stack.run(task_id)
    assert stack.world.executed("memory_search") == []
    assert events_of(events, "tool")[0].behavior == "unknown"


def test_f10_paid_provider_needs_its_feature_switch(tmp_path: Path) -> None:
    from opendot_core.eval.fake_provider import ScriptedProvider

    paid = ScriptedProvider([text_turn("paid")], name="openai_key", paid=True)
    stack = build_stack(tmp_path, extra_providers=[paid])
    loop = AgentLoop(
        stack.database, stack.registry, stack.router, stack.meter, stack.rules, PromptPacker(), stack.tools,
        stack.reviewer, provider_name="openai_key", clock=stack.clock,
    )
    task_id = loop.start_task("hello")
    list(loop.run(task_id))
    assert loop.task(task_id).state is TaskState.FAILED and paid.calls == 0
    stack.registry.settings.features.enabled.add("paid_fallback_when_plan_runs_out")
    task_id = loop.start_task("hello")
    list(loop.run(task_id))
    assert loop.task(task_id).state is TaskState.COMPLETED and paid.calls == 1


def test_f12_started_step_that_now_asks_is_handed_off_not_reproposed(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("reminder_set", {"text": "x", "minutes": 5}), text_turn("ok")])
    task_id = stack.loop.start_task("remind me")

    class Crash(BaseException):
        pass

    def crash(*args: object, **kwargs: object) -> str:
        raise Crash

    real_run = stack.tools.run
    stack.tools.run = crash  # type: ignore[method-assign]
    with pytest.raises(Crash):
        stack.run(task_id)
    stack.tools.run = real_run  # type: ignore[method-assign]
    restarted = stack.restart()
    restarted.rules.add_rule(tool="reminder_set", action="create", behavior="ask", max_sensitivity="personal")
    restarted.loop.resume_all()
    assert restarted.loop.task(task_id).state is TaskState.HANDED_OFF
    assert restarted.approvals.list_pending() == []
    assert restarted.world.executed("reminder_set") == []


def test_f12_uncertainty_survives_a_pause_before_the_rerun(tmp_path: Path) -> None:
    stack = build_stack(tmp_path, script=[tool_turn("reminder_set", {"text": "x", "minutes": 5}), text_turn("ok")])
    task_id = stack.loop.start_task("remind me")

    class Crash(BaseException):
        pass

    def crash(*args: object, **kwargs: object) -> str:
        raise Crash

    real_run = stack.tools.run
    stack.tools.run = crash  # type: ignore[method-assign]
    with pytest.raises(Crash):
        stack.run(task_id)
    stack.tools.run = real_run  # type: ignore[method-assign]
    restarted = stack.restart()
    restarted.loop.pause("user")  # still auto on resume, but paused before the rerun
    restarted.loop.resume_all()
    assert restarted.loop.task(task_id).state is TaskState.PAUSED
    restarted.rules.add_rule(tool="reminder_set", action="create", behavior="ask", max_sensitivity="personal")
    restarted.loop.unpause()
    restarted.run(task_id)
    assert restarted.loop.task(task_id).state is TaskState.HANDED_OFF
    assert restarted.approvals.list_pending() == []
    assert restarted.world.executed("reminder_set") == []
