"""Reviewer pass and anomaly monitor (M2 task 2.7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opendot_core.agent.reviewer import Proposal, Reviewer, known_recipient_domains, recipient_domains
from opendot_core.db import Database
from opendot_core.eval.fake_provider import ScriptedProvider, error_turn, text_turn
from opendot_core.policy import ApprovalService
from opendot_core.providers.errors import UsageLimitExceeded
from opendot_core.providers.registry import ProviderRegistry, ProviderSettings
from opendot_core.router import Router
from opendot_core.usage.meter import UsageMeter


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "opendot.db")
    db.migrate()
    return db


def draft(**arguments: str) -> Proposal:
    args = {"to": "bob@example.com", "subject": "Lunch", "body": "Free Friday?", **arguments}
    return Proposal(
        tool="gmail_draft_create",
        arguments=args,
        intent_tool="message_draft",
        intent_action="create",
        user_message="draft an email to bob@example.com about lunch",
    )


def test_new_domain_is_a_concern_known_domain_is_ok(database: Database) -> None:
    reviewer = Reviewer(database, use_model=False)
    note = reviewer.review(draft())
    assert note.verdict == "concern" and any("example.com" in r for r in note.reasons)
    service = ApprovalService(database)
    approval = service.propose(actor="owner", action_type="gmail_draft_create", preview={"to": "amy@example.com"})
    service.approve(approval.id, actor="owner")
    assert known_recipient_domains(database) == {"example.com"}
    assert reviewer.review(draft()).verdict == "ok"


def test_pending_approvals_do_not_make_a_domain_known(database: Database) -> None:
    ApprovalService(database).propose(actor="owner", action_type="gmail_draft_create", preview={"to": "x@evil.test"})
    assert known_recipient_domains(database) == set()


@pytest.mark.parametrize(
    "body",
    ["password: hunter2hunter2", "key sk-abcdefghijklmnopqrstuvwx", "card 4111 1111 1111 1111", "-----BEGIN RSA PRIVATE KEY-----"],
)
def test_secrets_are_blocked(database: Database, body: str) -> None:
    assert Reviewer(database, use_model=False).review(draft(body=body)).verdict == "block"


def test_deny_list_intent_is_blocked(database: Database) -> None:
    proposal = Proposal(tool="gmail_delete_message", arguments={}, intent_tool="gmail", intent_action="delete")
    assert Reviewer(database, use_model=False).review(proposal).verdict == "block"


def test_unnamed_recipient_links_and_many_recipients(database: Database) -> None:
    reviewer = Reviewer(database, use_model=False)
    note = reviewer.review(draft(to="mallory@example.com", body="see https://x.test"))
    assert note.verdict == "concern"
    assert any("mallory@example.com" in r for r in note.reasons) and any("link" in r for r in note.reasons)
    many = Proposal(tool="t", arguments={"to": [f"p{n}@a.test" for n in range(6)]})
    assert any("6 recipients" in r for r in reviewer.review(many).reasons)


def test_recipient_domains_parses_lists_and_dedupes() -> None:
    assert recipient_domains(["A@X.test, b@x.test", "c@y.test"]) == ["x.test", "y.test"]


def _model_reviewer(database: Database, turns: list) -> tuple[Reviewer, ScriptedProvider, UsageMeter]:
    provider = ScriptedProvider(turns)
    registry = ProviderRegistry(ProviderSettings(enabled={"chatgpt_plan"}))
    registry.register(provider)
    meter = UsageMeter(database)
    return Reviewer(database, registry, Router(provider.list_models()), meter), provider, meter


def test_model_pass_runs_at_mid_tier_with_structured_output(database: Database) -> None:
    answer = json.dumps({"verdict": "concern", "reasons": ["Tone is odd."]})
    reviewer, provider, meter = _model_reviewer(database, [text_turn(answer)])
    note = reviewer.review(draft(to="amy@example.com", body="hi"))
    request = provider.requests[0]
    assert request.model == "fake-terra" and request.effort == "medium" and request.structured_output
    assert note.verdict == "concern" and "Tone is odd." in note.reasons and note.model == "fake-terra"
    assert [line.key for line in meter.summary().by_job_type] == ["review"]


def test_model_cannot_downgrade_a_deterministic_concern(database: Database) -> None:
    reviewer, _, _ = _model_reviewer(database, [text_turn(json.dumps({"verdict": "ok", "reasons": []}))])
    assert reviewer.review(draft()).verdict == "concern"


def test_block_skips_the_model(database: Database) -> None:
    reviewer, provider, _ = _model_reviewer(database, [])
    assert reviewer.review(draft(body="password: hunter2hunter2")).verdict == "block"
    assert provider.calls == 0


def test_model_errors_become_a_concern_never_a_switch(database: Database) -> None:
    reviewer, provider, _ = _model_reviewer(database, [error_turn(UsageLimitExceeded("429"))])
    note = reviewer.review(draft(to="amy@example.com", body="hi"))
    assert note.verdict == "concern" and provider.calls == 1


def test_unreadable_model_answer_is_a_concern(database: Database) -> None:
    reviewer, _, _ = _model_reviewer(database, [text_turn("sure, looks fine")])
    assert reviewer.review(draft(to="amy@example.com", body="hi")).verdict == "concern"
