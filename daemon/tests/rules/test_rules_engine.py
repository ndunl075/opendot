from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from opendot_core.audit import AuditLog
from opendot_core.composio import ACTION_TYPE as COMPOSIO_ACTION_TYPE
from opendot_core.db import Database
from opendot_core.mcp_server import MCP_TOOL_NAMES
from opendot_core.policy import ApprovalService
from opendot_core.rules import Behavior, RuleEngine, RuleError, ToolIntent
from opendot_core.rules.defaults import DEFAULT_BY_TOOL


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "opendot.db")
    db.migrate()
    return db


@pytest.fixture
def engine(database: Database) -> RuleEngine:
    return RuleEngine(database)


def intent(tool: str, action: str = "call", **kwargs) -> ToolIntent:
    kwargs.setdefault("sensitivity", "personal")
    return ToolIntent(tool=tool, action=action, **kwargs)


def insert_raw(database: Database, **fields) -> None:
    row = {
        "id": "raw-1",
        "tool": "*",
        "action": "*",
        "target": None,
        "max_sensitivity": "secret",
        "behavior": "auto",
        "created_by": "test",
        "created_at": datetime.now(UTC).isoformat(),
        "note": None,
        "max_cost_credits": None,
    }
    row.update(fields)
    with database.connect() as connection:
        connection.execute(
            f"INSERT INTO rules ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values())
        )
        connection.commit()


def approved(database: Database, action_type: str, preview: dict, *, consume: bool = False):
    service = ApprovalService(database)
    approval = service.propose(actor="agent", action_type=action_type, preview=preview)
    issued = service.approve(approval.id, actor="agent")
    if consume:
        service.consume(approval.id, actor="agent", token=issued.token)
    return approval


# --- behaviors -------------------------------------------------------------------------


def test_each_behavior(engine: RuleEngine) -> None:
    for behavior in Behavior:
        engine.add_rule(tool=f"t_{behavior.value}", behavior=behavior)
        decision = engine.decide(intent(f"t_{behavior.value}", user_preapproved=True))
        expected = Behavior.AUTO if behavior is Behavior.AUTO_IF_PREAPPROVED else behavior
        assert decision.behavior is expected
        assert decision.rule_id is not None


def test_unmatched_asks(engine: RuleEngine) -> None:
    decision = engine.decide(intent("brand_new_tool"))
    assert decision.behavior is Behavior.ASK
    assert decision.rule_id is None
    assert not decision.locked


def test_preapproved_semantics(engine: RuleEngine) -> None:
    engine.add_rule(tool="sender", behavior=Behavior.AUTO_IF_PREAPPROVED)
    assert engine.decide(intent("sender", user_preapproved=True)).behavior is Behavior.AUTO
    not_pre = engine.decide(intent("sender"))
    assert not_pre.behavior is Behavior.ASK
    assert not_pre.rule_id is not None


def test_sensitivity_and_cost_matching(engine: RuleEngine) -> None:
    engine.add_rule(tool="t", behavior="auto", max_sensitivity="personal", max_cost_credits=1.0)
    assert engine.decide(intent("t", sensitivity="public", cost_credits=0.5)).behavior is Behavior.AUTO
    assert engine.decide(intent("t", sensitivity="sensitive")).behavior is Behavior.ASK
    assert engine.decide(intent("t", cost_credits=2.0)).behavior is Behavior.ASK


# --- specificity ---------------------------------------------------------------------


def test_most_specific_wins_over_broader_stricter(engine: RuleEngine) -> None:
    engine.add_rule(tool="t", behavior="ask")
    engine.add_rule(tool="t", action="read", behavior="auto")
    assert engine.decide(intent("t", "read")).behavior is Behavior.AUTO
    assert engine.decide(intent("t", "write")).behavior is Behavior.ASK


def test_target_more_specific_than_glob(engine: RuleEngine) -> None:
    engine.add_rule(tool="t", target="*@example.com", behavior="ask")
    literal = engine.add_rule(tool="t", target="a@example.com", behavior="auto")
    decision = engine.decide(intent("t", target="a@example.com"))
    assert decision.rule_id == literal.id
    assert engine.decide(intent("t", target="b@example.com")).behavior is Behavior.ASK
    assert engine.decide(intent("t", target=None)).behavior is Behavior.ASK  # target rules need a target


def test_tie_goes_to_stricter(engine: RuleEngine) -> None:
    engine.add_rule(tool="t", behavior="auto")
    engine.add_rule(tool="t", behavior="ask")
    assert engine.decide(intent("t")).behavior is Behavior.ASK
    engine.add_rule(tool="t", behavior="handoff")
    assert engine.decide(intent("t")).behavior is Behavior.HANDOFF


def test_user_rule_needs_to_be_more_specific_to_loosen_default(engine: RuleEngine) -> None:
    engine.add_rule(tool="message_draft", behavior="auto")  # same specificity as default ask: stricter wins
    assert engine.decide(intent("message_draft", sensitivity="secret")).behavior is Behavior.ASK
    engine.add_rule(tool="message_draft", target="me@example.com", behavior="auto")
    assert engine.decide(intent("message_draft", target="me@example.com")).behavior is Behavior.AUTO


# --- defaults ---------------------------------------------------------------------------


def test_every_mcp_tool_has_an_explicit_default() -> None:
    missing = set(MCP_TOOL_NAMES) - set(DEFAULT_BY_TOOL)
    assert not missing, f"MCP tools without a rules default: {sorted(missing)}"
    assert not set(DEFAULT_BY_TOOL) - set(MCP_TOOL_NAMES), "defaults for tools that do not exist"


def test_every_mcp_tool_default_resolves_to_its_entry(engine: RuleEngine) -> None:
    for tool in sorted(MCP_TOOL_NAMES):
        decision = engine.decide(intent(tool, "call", sensitivity="public"))
        assert decision.rule_id == f"default:{tool}", tool
        assert decision.behavior.value == DEFAULT_BY_TOOL[tool].behavior


@pytest.mark.parametrize(
    ("tool", "behavior"),
    [
        ("memory_search", Behavior.AUTO),
        ("pull_requests_get", Behavior.AUTO),
        ("remember", Behavior.AUTO),
        ("reminder_set", Behavior.AUTO),
        ("task_upsert", Behavior.AUTO),
        ("calendar_event_propose", Behavior.ASK),
        ("message_draft", Behavior.ASK),
        ("github_issue_propose", Behavior.ASK),
        ("message_send_propose", Behavior.ASK),
        ("composio_execute", Behavior.ASK),
    ],
)
def test_section_10_defaults(engine: RuleEngine, tool: str, behavior: Behavior) -> None:
    assert engine.decide(intent(tool, sensitivity="personal", user_preapproved=True)).behavior is behavior


def test_sensitive_memory_write_is_not_auto(engine: RuleEngine) -> None:
    assert engine.decide(intent("remember", sensitivity="sensitive")).behavior is Behavior.ASK


# --- deny list --------------------------------------------------------------------------

DENY_ACTIONS = ["delete_external", "spend_money", "security_change", "change_password", "credential_change"]


@pytest.mark.parametrize("action", DENY_ACTIONS)
def test_deny_list_is_handoff_locked(engine: RuleEngine, action: str) -> None:
    decision = engine.decide(intent("anything", action, user_preapproved=True))
    assert decision.behavior is Behavior.HANDOFF
    assert decision.locked
    assert decision.rule_id and decision.rule_id.startswith("deny:")


@pytest.mark.parametrize("slug", ["GMAIL_DELETE_MESSAGE", "STRIPE_CREATE_PAYMENT", "GITHUB_DELETE_A_REPOSITORY"])
def test_composio_outside_app_slugs_hit_deny_list(engine: RuleEngine, slug: str) -> None:
    decision = engine.decide(intent("composio_execute", slug))
    assert decision.behavior is Behavior.HANDOFF and decision.locked


def test_composio_harmless_slug_is_not_denied(engine: RuleEngine) -> None:
    decision = engine.decide(intent("composio_execute", "GMAIL_CREATE_EMAIL_DRAFT"))
    assert decision.behavior is Behavior.ASK and not decision.locked


def test_local_forget_is_not_deny_listed(engine: RuleEngine) -> None:
    assert not engine.decide(intent("forget", "delete")).locked


@pytest.mark.parametrize("action", DENY_ACTIONS + ["delete*", "password_change"])
def test_add_rule_rejected_for_deny_list(engine: RuleEngine, action: str) -> None:
    with pytest.raises(RuleError, match="deny list"):
        engine.add_rule(tool="anything", action=action, behavior="auto")
    with pytest.raises(RuleError, match="deny list"):
        engine.add_rule(tool="*", action=action, behavior="ask")


def test_update_rule_cannot_retarget_deny_list(engine: RuleEngine) -> None:
    rule = engine.add_rule(tool="t", behavior="auto")
    with pytest.raises(RuleError, match="deny list"):
        engine.update_rule(rule.id, action="delete_external")


def test_direct_db_insert_cannot_override_deny_list(database: Database, engine: RuleEngine) -> None:
    insert_raw(database, id="raw-a", tool="*", action="*", behavior="auto")
    insert_raw(database, id="raw-b", tool="anything", action="delete_external", target="x", behavior="auto")
    insert_raw(database, id="raw-c", tool="composio_execute", action="GMAIL_DELETE_MESSAGE", behavior="auto")
    assert engine.decide(intent("anything", "delete_external", target="x")).behavior is Behavior.HANDOFF
    assert engine.decide(intent("composio_execute", "GMAIL_DELETE_MESSAGE")).behavior is Behavior.HANDOFF
    assert engine.decide(intent("x", "spend_money")).locked


def test_wildcard_auto_rule_is_inert_for_deny_list(engine: RuleEngine) -> None:
    engine.add_rule(tool="*", behavior="auto")
    assert engine.decide(intent("x", "change_password")).behavior is Behavior.HANDOFF
    assert engine.decide(intent("x", "read")).behavior is Behavior.AUTO


def test_list_rules_includes_locked_deny_entries(engine: RuleEngine) -> None:
    engine.add_rule(tool="t", behavior="ask")
    rules = engine.list_rules()
    locked = [rule for rule in rules if rule.locked]
    assert len(locked) == 4
    assert all(rule.behavior is Behavior.HANDOFF for rule in locked)
    assert any(not rule.locked and rule.tool == "t" for rule in rules)
    assert {rule.tool for rule in engine.list_rules(include_defaults=False) if not rule.locked} == {"t"}


def test_locked_and_default_entries_cannot_be_changed(engine: RuleEngine) -> None:
    for rule_id in ("deny:spend_money", "default:remember"):
        with pytest.raises(RuleError):
            engine.update_rule(rule_id, behavior="auto")
        with pytest.raises(RuleError):
            engine.delete_rule(rule_id)


# --- always_allow ----------------------------------------------------------------------


def test_always_allow_creates_auto_rule(database: Database, engine: RuleEngine) -> None:
    approval = approved(database, "gmail_draft_create", {"to": "a@example.com", "subject": "s", "body": "b"})
    rule = engine.always_allow(approval.id, "user:ui")
    assert (rule.tool, rule.target, rule.behavior, rule.created_by) == (
        "message_draft",
        "a@example.com",
        Behavior.AUTO,
        "user:ui",
    )
    assert engine.decide(intent("message_draft", target="a@example.com")).rule_id == rule.id
    assert engine.decide(intent("message_draft", target="a@example.com")).behavior is Behavior.AUTO
    assert engine.decide(intent("message_draft", target="other@example.com")).behavior is Behavior.ASK


def test_always_allow_accepts_consumed(database: Database, engine: RuleEngine) -> None:
    approval = approved(database, "github_issue_create", {"repository": "o/r", "title": "t"}, consume=True)
    assert engine.always_allow(approval.id, "user:ui").tool == "github_issue_propose"


def test_always_allow_calendar(database: Database, engine: RuleEngine) -> None:
    approval = approved(database, "calendar_event_create", {"calendar_id": "primary", "summary": "x"})
    assert engine.always_allow(approval.id, "u").target == "primary"


def test_always_allow_refuses_pending_rejected_and_missing(database: Database, engine: RuleEngine) -> None:
    service = ApprovalService(database)
    pending = service.propose(actor="agent", action_type="gmail_draft_create", preview={"to": "a@b.c"})
    with pytest.raises(RuleError, match="not approved"):
        engine.always_allow(pending.id, "u")
    service.reject(pending.id, actor="agent")
    with pytest.raises(RuleError, match="not approved"):
        engine.always_allow(pending.id, "u")
    with pytest.raises(RuleError, match="does not exist"):
        engine.always_allow("nope", "u")


@pytest.mark.parametrize(
    ("action_type", "preview"),
    [
        ("gmail_message_send", {"to": "a@b.c", "subject": "s", "body": "b"}),
        ("github_pr_comment_create", {"repository": "o/r", "pull_number": 1, "body": "b"}),
        ("memory_forget", {"memory_id": "m"}),
        ("database_restore", {}),
        (COMPOSIO_ACTION_TYPE, {"slug": "GMAIL_DELETE_MESSAGE"}),
        (COMPOSIO_ACTION_TYPE, {"slug": "STRIPE_CREATE_PAYMENT"}),
        ("unknown_type", {"to": "a"}),
    ],
)
def test_always_allow_refused_for_deny_send_and_unknown(
    database: Database, engine: RuleEngine, action_type: str, preview: dict
) -> None:
    approval = approved(database, action_type, preview)
    with pytest.raises(RuleError):
        engine.always_allow(approval.id, "u")
    assert not [rule for rule in engine.list_rules(include_defaults=False) if not rule.locked]


def test_always_allow_composio_safe_slug(database: Database, engine: RuleEngine) -> None:
    approval = approved(database, COMPOSIO_ACTION_TYPE, {"slug": "GMAIL_CREATE_EMAIL_DRAFT"})
    rule = engine.always_allow(approval.id, "u")
    assert rule.tool == "composio_execute" and rule.action == "GMAIL_CREATE_EMAIL_DRAFT"
    assert engine.decide(intent("composio_execute", "GMAIL_CREATE_EMAIL_DRAFT")).behavior is Behavior.AUTO


# --- audit, persistence, migration ---------------------------------------------------


def test_audit_decision_has_no_content(database: Database, engine: RuleEngine) -> None:
    rule = engine.add_rule(tool="t", behavior="ask", note="n")
    it = intent("t", target="secret-person@example.com")
    record_id = engine.audit_decision(it, engine.decide(it), actor="agent")
    record = next(r for r in AuditLog(database).recent() if r.id == record_id)
    assert record.tool == "rule_decision"
    assert record.result["tool"] == "t"
    assert record.result["behavior"] == "ask"
    assert record.result["rule_id"] == rule.id
    assert record.result["reason"]
    with database.connect() as connection:
        raw = connection.execute("SELECT arguments_json, result_json FROM tool_runs WHERE id = ?", (record_id,)).fetchone()
    assert "secret-person" not in raw["arguments_json"] + raw["result_json"]
    assert AuditLog(database).verify()


def test_rules_persist_across_engines(tmp_path: Path) -> None:
    path = tmp_path / "p.db"
    rule = RuleEngine(Database(path)).add_rule(tool="t", behavior="auto", target="x", note="keep")
    reopened = RuleEngine(Database(path))
    stored = [r for r in reopened.list_rules(include_defaults=False) if r.id == rule.id]
    assert stored and stored[0].note == "keep" and stored[0].target == "x"
    assert reopened.decide(intent("t", target="x")).rule_id == rule.id
    reopened.update_rule(rule.id, behavior="handoff", note="changed")
    assert RuleEngine(Database(path)).decide(intent("t", target="x")).behavior is Behavior.HANDOFF
    reopened.delete_rule(rule.id)
    assert RuleEngine(Database(path)).decide(intent("t", target="x")).rule_id is None


def test_migration_applies_to_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    database = Database(path)
    database.migrate()
    with database.connect() as connection:
        connection.execute("DROP TABLE rules")
        connection.execute("DELETE FROM schema_migrations WHERE filename = '0020_rules.sql'")
        connection.commit()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'rules'").fetchone() is None
    database.migrate()
    engine = RuleEngine(database)
    engine.add_rule(tool="t", behavior="ask")
    assert engine.decide(intent("t")).behavior is Behavior.ASK


def test_invalid_inputs_rejected(engine: RuleEngine) -> None:
    with pytest.raises(RuleError):
        engine.add_rule(tool="t", behavior="sometimes")
    with pytest.raises(RuleError):
        engine.add_rule(tool="t", behavior="auto", max_sensitivity="top")
    with pytest.raises(RuleError):
        engine.add_rule(tool=" ", behavior="auto")


@pytest.mark.parametrize(
    ("tool", "action"),
    [("gmail", "delete"), ("google_calendar", "event_delete"), ("github", "remove_collaborator"), ("stripe", "pay")],
)
def test_outside_app_deletes_and_payments_hit_deny_list(engine: RuleEngine, tool: str, action: str) -> None:
    decision = engine.decide(intent(tool, action))
    assert decision.behavior is Behavior.HANDOFF and decision.locked


@pytest.mark.parametrize(("tool", "action"), [("forget", "delete"), ("reminder_set", "remove"), ("task_complete", "delete")])
def test_local_tools_are_not_matched_by_keyword(engine: RuleEngine, tool: str, action: str) -> None:
    assert not engine.decide(intent(tool, action)).locked


def test_add_rule_accepts_a_rule_object_and_rejects_deny_list(engine: RuleEngine) -> None:
    from opendot_core.rules import Rule

    denied = Rule(id="x", tool="gmail", action="delete", behavior=Behavior.AUTO, created_by="user",
                  created_at=datetime.now(UTC))
    with pytest.raises(RuleError, match="deny list"):
        engine.add_rule(denied)
    allowed = Rule(id="y", tool="reminder_set", action="create", behavior=Behavior.AUTO, created_by="user",
                   created_at=datetime.now(UTC), max_sensitivity="personal")
    stored = engine.add_rule(allowed)
    assert stored.id == "y" and engine.list_rules(include_defaults=False)[0].id == "y"
    with pytest.raises(RuleError):
        engine.add_rule()


@pytest.mark.parametrize(
    ("tool", "action"),
    [
        ("message_send_propose", "create"),
        ("gmail", "send"),
        ("slack", "post_message"),
        ("github_pr_comment_create", "create"),
        ("github", "comment_create"),
        ("gmail", "send_reply"),
        ("composio_execute", "SLACK_CHAT_POST_MESSAGE"),
        ("composio_execute", "GMAIL_REPLY_TO_EMAIL"),
        ("composio_execute", "GMAIL_SEND_EMAIL"),
        ("composio_execute", "GITHUB_CREATE_ISSUE_COMMENT"),
        ("composio_execute", "SLACK_SEND_MESSAGE"),
        ("composio_execute", "LINKEDIN_CREATE_POST"),
        ("composio_execute", "SLACK_CHAT_SCHEDULE_MESSAGE"),
        ("composio_execute", "SLACK_CHAT_UPDATE"),
        ("composio_execute", "GITHUB_UPDATE_ISSUE_COMMENT"),
        ("composio_execute", "GITHUB_MARK_DISCUSSION_COMMENT_AS_ANSWER"),
        ("composio_execute", "SLACK_PIN_MESSAGE"),
        ("composio_execute", "GMAIL_MODIFY_MESSAGE_AND_SEND"),
    ],
)
def test_send_and_post_ask_every_time_even_with_an_auto_rule(engine: RuleEngine, tool: str, action: str) -> None:
    engine.add_rule(tool=tool, action=action, behavior="auto")
    decision = engine.decide(intent(tool, action))
    assert decision.behavior is Behavior.ASK and decision.locked


def test_always_allow_uses_the_real_composio_action_type(database: Database, engine: RuleEngine) -> None:
    approval = approved(database, COMPOSIO_ACTION_TYPE, {"slug": "GITHUB_CREATE_ISSUE", "arguments": {}})
    rule = engine.always_allow(approval.id, "agent")
    assert rule.tool == "composio_execute" and rule.action == "GITHUB_CREATE_ISSUE"


@pytest.mark.parametrize(("tool", "action"), [("message_draft", "create"), ("calendar_event_propose", "create"), ("reminder_set", "send")])
def test_drafts_events_and_local_tools_can_still_be_auto(engine: RuleEngine, tool: str, action: str) -> None:
    engine.add_rule(tool=tool, action=action, behavior="auto")
    assert engine.decide(intent(tool, action)).behavior is Behavior.AUTO


@pytest.mark.parametrize(
    "slug",
    ["GITHUB_LIST_ISSUE_COMMENTS", "SLACK_FETCH_CONVERSATION_HISTORY", "GMAIL_FETCH_EMAILS", "SLACK_SEARCH_MESSAGES",
     "GMAIL_GET_EMAIL", "GMAIL_CREATE_EMAIL_DRAFT", "GOOGLECALENDAR_CREATE_EVENT", "GMAIL_MODIFY_MESSAGE",
     "GMAIL_ADD_LABEL_TO_EMAIL", "GMAIL_MARK_MESSAGE_AS_READ", "OUTLOOK_MOVE_MESSAGE"],
)
def test_reads_drafts_and_events_are_not_sends(engine: RuleEngine, slug: str) -> None:
    engine.add_rule(tool="composio_execute", action=slug, behavior="auto")
    assert engine.decide(intent("composio_execute", slug)).behavior is Behavior.AUTO
