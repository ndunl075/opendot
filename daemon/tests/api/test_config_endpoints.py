"""Rules, usage, settings, providers and backup endpoints on the real runtime (M4).

Built with ``build_agent_runtime`` and a scripted provider; only the OS keychain is replaced by an
in-memory fake. Every body is validated against the contract models.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from opendot_core.agent.runtime import AgentRuntime, build_agent_runtime
from opendot_core.api import models as m
from opendot_core.api.contract import ENDPOINTS
from opendot_core.eval.harness import build_world
from opendot_core.providers.registry import ProviderRegistry, ProviderSettings
from opendot_core.providers.types import Usage
from opendot_core.router.tiers import Job, Tier
from opendot_core.rules import ToolIntent
from opendot_core.secret_store import SecretStoreError

TOKEN = "config-endpoints-bearer-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SECRET_KEY = "sk-live-THIS-MUST-NEVER-LEAK-0123456789"


class FakeKeychain:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_required(self, name: str) -> str:
        if name not in self.values:
            raise SecretStoreError(f"missing {name}")
        return self.values[name]

    def store(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class Harness:
    """A world on disk plus helpers to start a (new) app instance on the same database."""

    def __init__(self, tmp_path: Path) -> None:
        self.stack = build_world(tmp_path)
        self.keychain = FakeKeychain()
        self.tmp_path = tmp_path
        self.runtime, self.client = self.start()

    def start(self) -> tuple[AgentRuntime, TestClient]:
        registry = ProviderRegistry(ProviderSettings())
        registry.register(self.stack.world.provider)
        runtime = build_agent_runtime(
            self.stack.database, registry, self.stack.tools, api_token=TOKEN, clock=self.stack.world.clock,
            secret_store=self.keychain,
        )
        return runtime, TestClient(runtime.app)

    def restart(self) -> None:
        self.runtime, self.client = self.start()

    def get(self, path: str, **kwargs):  # noqa: ANN003, ANN201
        return self.client.get(f"/v1{path}", headers=AUTH, **kwargs)

    def put(self, path: str, body: object):  # noqa: ANN201
        return self.client.put(f"/v1{path}", headers=AUTH, json=body)

    def post(self, path: str, body: object | None = None):  # noqa: ANN201
        return self.client.post(f"/v1{path}", headers=AUTH, json=body if body is not None else {})

    def delete(self, path: str):  # noqa: ANN201
        return self.client.delete(f"/v1{path}", headers=AUTH)


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


# -- every endpoint in these areas is real --------------------------------------------------------

AREAS = ("rules_", "rule_", "usage_", "settings_", "provider", "feature", "spend_", "backup_")


def test_no_endpoint_in_these_areas_answers_501(h: Harness, tmp_path: Path) -> None:
    from opendot_core.api.web import create_web_app

    web = TestClient(create_web_app(h.runtime.app, token=TOKEN, ui_dist=None), base_url="http://127.0.0.1")
    checked = 0
    for endpoint in ENDPOINTS:
        if not endpoint.name.startswith(AREAS):
            continue
        path = endpoint.path.replace("{rule_id}", "nope").replace("{provider}", "openai_key").replace(
            "{feature}", "claude_as_reviewer"
        )
        response = web.request(endpoint.method, path, headers=AUTH, json={} if endpoint.request else None)
        assert response.status_code != 501, endpoint.name
        checked += 1
    assert checked == 19


def test_every_endpoint_needs_the_bearer_token(h: Harness) -> None:
    assert h.client.get("/v1/rules").status_code == 401
    assert h.client.put("/v1/providers/openai_key/api-key", json={"api_key": "x"}).status_code == 401
    assert h.client.post("/v1/backup/restore", json={"backup_id": "a", "confirm": "restore"}).status_code == 401


# -- rules -----------------------------------------------------------------------------------


def _rules(h: Harness) -> list[m.Rule]:
    response = h.get("/rules")
    assert response.status_code == 200
    return m.RuleList.model_validate(response.json()).rules


def test_rules_list_shows_defaults_and_locked_deny_list(h: Harness) -> None:
    rules = _rules(h)
    deny = [r for r in rules if r.core_deny]
    assert {r.id for r in deny} == {
        "deny:delete_external", "deny:spend_money", "deny:security_change", "deny:credential_change"
    }
    assert all(r.locked and r.behavior == "handoff" for r in deny)
    assert any(r.id == "default:message_draft" and r.behavior == "ask" and not r.core_deny for r in rules)


def test_rule_create_update_delete_and_decision(h: Harness) -> None:
    created = h.post("/rules", {"name": "Drafts are fine", "action": "message_draft", "behavior": "auto",
                                "description": "I review drafts anyway"})
    assert created.status_code == 200
    rule = m.Rule.model_validate(created.json())
    assert (rule.name, rule.action, rule.behavior, rule.enabled, rule.locked) == (
        "Drafts are fine", "message_draft", "auto", True, False
    )
    assert rule.description == "I review drafts anyway"
    intent = ToolIntent(tool="message_draft", action="create", sensitivity="personal")
    assert h.runtime.rules.decide(intent).behavior.value == "auto"

    # the same rule after an app restart
    h.restart()
    assert next(r for r in _rules(h) if r.id == rule.id).name == "Drafts are fine"

    updated = h.put(f"/rules/{rule.id}", {"name": "Drafts", "behavior": "ask"})
    assert updated.status_code == 200
    assert m.Rule.model_validate(updated.json()).behavior == "ask"
    assert h.runtime.rules.decide(intent).behavior.value == "ask"

    disabled = h.put(f"/rules/{rule.id}", {"enabled": False, "behavior": "auto"})
    assert m.Rule.model_validate(disabled.json()).enabled is False
    # a disabled rule is ignored: the built-in default (ask) applies
    assert h.runtime.rules.decide(intent).behavior.value == "ask"
    assert h.runtime.rules.decide(intent).rule_id == "default:message_draft"
    h.put(f"/rules/{rule.id}", {"enabled": True})
    assert h.runtime.rules.decide(intent).behavior.value == "auto"

    assert m.OkResponse.model_validate(h.delete(f"/rules/{rule.id}").json()).ok
    assert all(r.id != rule.id for r in _rules(h))
    assert h.delete(f"/rules/{rule.id}").status_code == 404
    assert h.put(f"/rules/{rule.id}", {"name": "x"}).status_code == 404


def test_user_rule_that_skips_asking_never_covers_secret_data(h: Harness) -> None:
    h.post("/rules", {"name": "auto", "action": "message_draft", "behavior": "auto"})
    secret = ToolIntent(tool="message_draft", action="create", sensitivity="secret")
    assert h.runtime.rules.decide(secret).behavior.value == "ask"


def test_tool_and_action_pattern_maps_to_the_engine(h: Harness) -> None:
    rule = m.Rule.model_validate(
        h.post("/rules", {"name": "Calendar reads", "action": "calendar_event_propose.create", "behavior": "ask"}).json()
    )
    assert rule.action == "calendar_event_propose.create"
    stored = h.runtime.rules.list_rules(include_defaults=False)[0]
    assert (stored.tool, stored.action) == ("calendar_event_propose", "create")


def test_deny_list_items_are_locked(h: Harness) -> None:
    assert h.put("/rules/deny:spend_money", {"behavior": "auto"}).status_code == 403
    assert h.put("/rules/deny:spend_money", {"enabled": False}).status_code == 403
    response = h.delete("/rules/deny:delete_external")
    assert response.status_code == 403
    assert m.ErrorResponse.model_validate(response.json()).code == "rule_locked"
    assert {r.id for r in _rules(h) if r.core_deny} >= {"deny:delete_external", "deny:spend_money"}
    # nothing can allow a deny-listed action either
    blocked = h.post("/rules", {"name": "spend", "action": "spend_money", "behavior": "auto"})
    assert blocked.status_code == 403 and m.ErrorResponse.model_validate(blocked.json()).code == "rule_locked"
    decision = h.runtime.rules.decide(ToolIntent(tool="shop", action="purchase", sensitivity="public"))
    assert decision.behavior.value == "handoff"


def test_builtin_default_rules_are_read_only(h: Harness) -> None:
    assert h.put("/rules/default:message_draft", {"behavior": "auto"}).status_code == 403
    assert h.delete("/rules/default:message_draft").status_code == 403


def test_rule_validation_errors(h: Harness) -> None:
    assert h.post("/rules", {"name": "", "action": "x", "behavior": "auto"}).status_code == 422
    assert h.post("/rules", {"name": "x", "action": "x", "behavior": "sometimes"}).status_code == 422
    rule = m.Rule.model_validate(h.post("/rules", {"name": "r", "action": "tool_x", "behavior": "ask"}).json())
    assert h.put(f"/rules/{rule.id}", {"behavior": "bogus"}).status_code == 422
    assert m.ErrorResponse.model_validate(h.put(f"/rules/{rule.id}", {"behavior": "bogus"}).json()).code == (
        "invalid_request"
    )


def test_always_allow_rules_show_their_approval(h: Harness) -> None:
    approval = h.runtime.approvals.propose(
        actor="owner", action_type="gmail_draft_create", preview={"to": "bob@example.com", "subject": "s"}
    )
    assert h.post(f"/approvals/{approval.id}/always-allow", {"behavior": "auto"}).status_code == 200
    rule = next(r for r in _rules(h) if r.created_from_approval_id == approval.id)
    assert rule.action == "message_draft" and rule.behavior == "auto" and not rule.locked


# -- usage -----------------------------------------------------------------------------------


def test_usage_summary_by_task_day_and_job_type(h: Harness) -> None:
    task_id = h.runtime.loop.start_task("write the weekly summary of everything", job_type="chat")
    h.runtime.meter.record(
        task_id=task_id, job_type="chat", model="gpt-5.6-luna", effort="low",
        usage=Usage(input_tokens=1_000_000, output_tokens=0),
    )
    h.runtime.meter.record(
        task_id="orphan-task", job_type="review", model="gpt-5.6-terra", effort="medium",
        usage=Usage(input_tokens=1_000_000, output_tokens=0),
    )
    response = h.get("/usage", params={"days": 3})
    assert response.status_code == 200
    usage = m.UsageSummary.model_validate(response.json())
    assert usage.days == 3 and len(usage.by_day) == 3
    assert usage.total_credits == pytest.approx(55.0) and usage.today_credits == pytest.approx(55.0)
    assert usage.by_day[-1].credits == pytest.approx(55.0) and usage.by_day[0].credits == 0
    titles = {t.task_id: t.title for t in usage.by_task}
    assert titles[task_id].startswith("write the weekly summary") and titles["orphan-task"] == "orphan-task"
    assert {j.job_type: j.credits for j in usage.by_job_type} == {
        "chat": pytest.approx(5.0), "review": pytest.approx(50.0)
    }
    assert usage.budgets.daily_hard_stop and usage.budgets.daily_credits == 50.0
    assert usage.paused_for_budget is True and usage.daily_budget_remaining == 0
    assert usage.manage_usage_url.startswith("https://") and usage.plan_label
    assert h.get("/usage", params={"days": 0}).status_code == 422


def test_usage_budgets_get_set_and_persist(h: Harness) -> None:
    assert m.UsageBudgets.model_validate(h.get("/usage/budgets").json()) == m.UsageBudgets(
        daily_credits=50.0, task_credits=5.0, daily_hard_stop=True
    )
    saved = h.put("/usage/budgets", {"daily_credits": 80, "task_credits": 8})
    assert saved.status_code == 200
    assert m.UsageBudgets.model_validate(saved.json()).daily_credits == 80
    # a missing budget keeps its value
    assert m.UsageBudgets.model_validate(h.put("/usage/budgets", {"task_credits": 9}).json()).daily_credits == 80
    h.restart()
    after = m.UsageBudgets.model_validate(h.get("/usage/budgets").json())
    assert (after.daily_credits, after.task_credits) == (80, 9)
    assert h.runtime.meter.get_budgets().daily_credits == 80


def test_daily_hard_stop_stays(h: Harness) -> None:
    response = h.put("/usage/budgets", {"daily_credits": 10, "daily_hard_stop": False})
    assert response.status_code == 422
    assert m.ErrorResponse.model_validate(response.json()).code == "hard_stop_required"
    assert h.runtime.meter.get_budgets().daily_credits == 50.0
    assert h.put("/usage/budgets", {"daily_credits": -1}).status_code == 422
    # lowering the budget stops plan requests right away
    h.put("/usage/budgets", {"daily_credits": 1})
    h.runtime.meter.record(
        task_id="t", job_type="chat", model="gpt-5.6-terra", effort="low", usage=Usage(input_tokens=1_000_000)
    )
    from opendot_core.usage.errors import DailyBudgetExceeded

    with pytest.raises(DailyBudgetExceeded):
        h.runtime.meter.check_before_request("t")


# -- settings --------------------------------------------------------------------------------


def _settings(h: Harness) -> m.Settings:
    response = h.get("/settings")
    assert response.status_code == 200
    return m.Settings.model_validate(response.json())


def test_settings_defaults(h: Harness) -> None:
    settings = _settings(h)
    assert settings.keep_awake.enabled is False and settings.quiet_hours.enabled is False
    assert settings.tier_overrides == [] and settings.auto_top_tier is False and settings.style_preset == "concise"
    by_name = {p.provider: p for p in settings.providers}
    assert set(by_name) == {"chatgpt_plan", "openai_key", "anthropic_key", "openrouter", "local"}
    assert by_name["chatgpt_plan"].enabled is True
    assert not any(by_name[n].enabled for n in ("openai_key", "anthropic_key", "openrouter", "local"))
    assert all(by_name[n].cost_warning for n in ("openai_key", "anthropic_key", "openrouter", "local"))


def test_settings_update_persists_across_a_new_app(h: Harness) -> None:
    body = {
        "keep_awake": {"enabled": True, "only_while_plugged_in": False},
        "quiet_hours": {"enabled": True, "start": "21:30", "end": "06:45", "timezone": "UTC"},
        "style_preset": "warm",
        "tier_overrides": [{"job_type": "chat", "tier": "terra", "effort": "medium"}],
        "auto_top_tier": True,
    }
    response = h.put("/settings", body)
    assert response.status_code == 200
    assert m.Settings.model_validate(response.json()).style_preset == "warm"
    # the live router changed at once
    assert h.runtime.router.auto_top is True
    route = h.runtime.router.route(Job.chat)
    assert (route.tier, route.effort) == (Tier.mid, "medium")

    h.restart()
    again = _settings(h)
    assert again.keep_awake == m.KeepAwakeSettings(enabled=True, only_while_plugged_in=False)
    assert again.quiet_hours.start == "21:30" and again.quiet_hours.end == "06:45"
    assert again.style_preset == "warm" and again.auto_top_tier is True
    assert again.tier_overrides == [m.ModelTierOverride(job_type="chat", tier="terra", effort="medium")]
    # startup loaded them into the new live router
    assert h.runtime.router.auto_top is True
    assert h.runtime.router.route(Job.chat).tier is Tier.mid
    # the keep-awake agent reads this key
    from opendot_core.settings_store import SettingsStore

    assert SettingsStore(h.stack.database).get("keep_awake", m.KeepAwakeSettings, None).enabled is True


def test_top_tier_override_still_asks_first(h: Harness) -> None:
    h.put("/settings", {"tier_overrides": [{"job_type": "chat", "tier": "sol"}]})
    route = h.runtime.router.route(Job.chat)
    assert route.tier is Tier.top and route.needs_approval is True
    h.put("/settings", {"auto_top_tier": True})
    assert h.runtime.router.route(Job.chat).needs_approval is False
    h.put("/settings", {"tier_overrides": [], "auto_top_tier": False})
    assert h.runtime.router.route(Job.chat).tier is Tier.cheap


def test_partial_update_changes_only_what_it_names(h: Harness) -> None:
    h.put("/settings", {"style_preset": "formal", "auto_top_tier": True})
    h.put("/settings", {"keep_awake": {"enabled": True}})
    settings = _settings(h)
    assert settings.style_preset == "formal" and settings.auto_top_tier is True and settings.keep_awake.enabled
    assert all(not p.enabled for p in settings.providers if p.provider != "chatgpt_plan")


@pytest.mark.parametrize(
    "body",
    [
        {"quiet_hours": {"enabled": True, "start": "25:00", "end": "06:00", "timezone": "UTC"}},
        {"quiet_hours": {"enabled": True, "start": "07:00", "end": "07:00", "timezone": "UTC"}},
        {"quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00", "timezone": "Mars/Base"}},
        {"tier_overrides": [{"job_type": "nonsense", "tier": "luna"}]},
        {"tier_overrides": [{"job_type": "chat", "tier": "luna"}, {"job_type": "chat", "tier": "sol"}]},
        {"provider_opt_ins": {"chatgpt_plan": False}},
        {"style_preset": "grumpy"},
    ],
)
def test_settings_update_rejects_bad_values(h: Harness, body: dict) -> None:
    response = h.put("/settings", body)
    assert response.status_code == 422
    assert m.ErrorResponse.model_validate(response.json()).code == "invalid_request"
    assert _settings(h).style_preset == "concise"


def test_settings_provider_opt_ins_update_the_live_registry_and_persist(h: Harness) -> None:
    h.put("/providers/openai_key/api-key", {"api_key": SECRET_KEY})
    assert h.runtime.loop.registry.settings.is_enabled("openai_key") is False  # saving a key enables nothing
    response = h.put("/settings", {"provider_opt_ins": {"openai_key": True}})
    assert response.status_code == 200
    assert {p.provider: p.enabled for p in m.Settings.model_validate(response.json()).providers}["openai_key"]
    registry = h.runtime.loop.registry
    assert registry.settings.is_enabled("openai_key") and "openai_key" in registry.names()
    h.restart()
    registry = h.runtime.loop.registry
    assert registry.settings.is_enabled("openai_key") and registry.settings.is_enabled("chatgpt_plan")
    assert "openai_key" in registry.names()  # re-registered on startup: enabled and has a key
    assert not registry.settings.is_enabled("anthropic_key")
    off = h.put("/settings", {"provider_opt_ins": {"openai_key": False}})
    assert not {p.provider: p.enabled for p in m.Settings.model_validate(off.json()).providers}["openai_key"]
    assert registry.settings.is_enabled("openai_key") is False


# -- providers -------------------------------------------------------------------------------


def test_saving_a_key_goes_to_the_keychain_and_enables_nothing(
    h: Harness, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    response = h.put("/providers/anthropic_key/api-key", {"api_key": SECRET_KEY})
    assert response.status_code == 200
    status = m.ProviderKeyStatus.model_validate(response.json())
    assert status.provider == "anthropic_key" and status.key_saved is True
    assert h.keychain.values["anthropic_api_key"] == SECRET_KEY
    settings = _settings(h)
    assert {p.provider: p.configured for p in settings.providers}["anthropic_key"] is True
    assert not {p.provider: p.enabled for p in settings.providers}["anthropic_key"]
    assert all(not f.enabled for f in m.FeatureSwitchList.model_validate(h.get("/providers/features").json()).features)
    assert h.runtime.loop.registry.settings.is_enabled("anthropic_key") is False
    for path in ("/settings", "/providers/features", "/providers/spend", "/backup", "/rules", "/usage"):
        assert SECRET_KEY not in h.get(path).text
    assert SECRET_KEY not in response.text
    assert SECRET_KEY not in caplog.text
    # not in SQLite either (main file and write-ahead log)
    for blob in tmp_path.rglob("opendot.db*"):
        assert SECRET_KEY.encode() not in blob.read_bytes()


def test_a_bad_key_request_does_not_echo_the_key(h: Harness) -> None:
    response = h.client.put(
        "/v1/providers/openai_key/api-key", headers=AUTH, content=f'{{"api_key": "{SECRET_KEY}", broken'
    )
    assert response.status_code == 422 and SECRET_KEY not in response.text
    blank = h.put("/providers/openai_key/api-key", {"api_key": "   "})
    assert blank.status_code == 422 and "openai_api_key" not in h.keychain.values


def test_removing_a_key(h: Harness) -> None:
    h.put("/providers/openrouter/api-key", {"api_key": SECRET_KEY})
    removed = h.delete("/providers/openrouter/api-key")
    assert removed.status_code == 200
    assert m.ProviderKeyStatus.model_validate(removed.json()).key_saved is False
    assert "openrouter_api_key" not in h.keychain.values
    assert m.ProviderKeyStatus.model_validate(h.delete("/providers/openrouter/api-key").json()).key_saved is False


def test_a_broken_keychain_is_a_503_without_the_key(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(name: str, value: str) -> None:
        raise RuntimeError(f"no backend; value was {value}")

    monkeypatch.setattr(h.keychain, "store", broken)
    response = h.put("/providers/openai_key/api-key", {"api_key": SECRET_KEY})
    assert response.status_code == 503 and SECRET_KEY not in response.text
    assert m.ErrorResponse.model_validate(response.json()).code == "keychain_unavailable"


def test_enabling_a_provider_is_explicit_and_persisted(h: Harness) -> None:
    h.put("/providers/openai_key/api-key", {"api_key": SECRET_KEY})
    assert "openai_key" not in h.runtime.loop.registry.names()
    response = h.put("/providers/openai_key/enabled", {"enabled": True})
    assert response.status_code == 200
    item = m.ProviderOptIn.model_validate(response.json())
    assert item.provider == "openai_key" and item.enabled and item.configured and item.cost_warning
    assert "paid_fallback_when_plan_runs_out" in item.feature_switches
    registry = h.runtime.loop.registry
    assert registry.settings.is_enabled("openai_key") and "openai_key" in registry.names()
    # enabling a provider switched no feature on
    assert all(not f.enabled for f in m.FeatureSwitchList.model_validate(h.get("/providers/features").json()).features)
    h.restart()
    assert h.runtime.loop.registry.settings.is_enabled("openai_key")
    off = h.put("/providers/openai_key/enabled", {"enabled": False})
    assert m.ProviderOptIn.model_validate(off.json()).enabled is False
    from opendot_core.providers.errors import ProviderDisabled

    with pytest.raises(ProviderDisabled):
        h.runtime.loop.registry.get("openai_key")


def test_provider_paths_are_checked(h: Harness) -> None:
    assert h.put("/providers/nope/enabled", {"enabled": True}).status_code == 404
    assert h.put("/providers/chatgpt_plan/enabled", {"enabled": False}).status_code == 422
    assert h.put("/providers/local/api-key", {"api_key": "k"}).status_code == 422
    assert h.delete("/providers/local/api-key").status_code == 422
    assert h.put("/providers/nope/spend-cap", {"cap_usd": 1}).status_code == 404
    assert h.put("/providers/local/spend-cap", {"cap_usd": 1}).status_code == 422
    assert h.put("/providers/features/nope", {"enabled": True}).status_code == 404
    assert h.put("/providers/openai_key/spend-cap", {"cap_usd": -1}).status_code == 422
    assert h.put("/providers/local/enabled", {"enabled": True}).status_code == 200
    assert "local" in h.runtime.loop.registry.names()  # a local model needs no key


def test_feature_switches_are_separate_from_providers(h: Harness) -> None:
    listing = m.FeatureSwitchList.model_validate(h.get("/providers/features").json()).features
    assert {f.name for f in listing} == {
        "paid_fallback_when_plan_runs_out", "claude_as_reviewer", "smarter_memory_search"
    }
    assert all(f.cost_warning and f.requirement and not f.enabled and not f.available for f in listing)
    response = h.put("/providers/features/claude_as_reviewer", {"enabled": True})
    state = m.FeatureSwitchState.model_validate(response.json())
    assert state.enabled is True and state.available is False  # needs anthropic_key enabled too
    assert h.runtime.loop.registry.settings.is_enabled("anthropic_key") is False
    h.put("/providers/anthropic_key/enabled", {"enabled": True})
    assert next(
        f for f in m.FeatureSwitchList.model_validate(h.get("/providers/features").json()).features
        if f.name == "claude_as_reviewer"
    ).available is True
    h.restart()
    assert h.runtime.loop.registry.settings.features.is_on("claude_as_reviewer")
    off = h.put("/providers/features/claude_as_reviewer", {"enabled": False})
    assert m.FeatureSwitchState.model_validate(off.json()).enabled is False


def test_spend_and_spend_cap(h: Harness) -> None:
    listing = m.ProviderSpendList.model_validate(h.get("/providers/spend").json()).providers
    assert [p.provider for p in listing] == ["openai_key", "anthropic_key", "openrouter"]
    assert all(p.cap_usd == 0 and p.spent_usd == 0 and not p.cap_set for p in listing)
    response = h.put("/providers/openai_key/spend-cap", {"cap_usd": 12.5})
    assert response.status_code == 200
    spend = m.ProviderSpend.model_validate(response.json())
    assert spend.cap_usd == 12.5 and spend.cap_set and spend.spent_usd == 0
    from opendot_core.providers.spend import SpendTracker

    tracker = SpendTracker(h.stack.database)
    tracker.record("openai_key", "gpt-4o", Usage(input_tokens=1_000_000, output_tokens=0))
    h.restart()
    listing = {p.provider: p for p in m.ProviderSpendList.model_validate(h.get("/providers/spend").json()).providers}
    assert listing["openai_key"].cap_usd == 12.5 and listing["openai_key"].spent_usd == pytest.approx(2.5)
    assert listing["anthropic_key"].cap_usd == 0


# -- backup ----------------------------------------------------------------------------------


def test_backup_create_list_and_key_in_keychain(h: Harness) -> None:
    empty = m.BackupStatus.model_validate(h.get("/backup").json())
    assert empty.backups == [] and empty.last_backup_at is None and empty.encryption_key_present is False
    created = h.post("/backup", {"verify": True})
    assert created.status_code == 200
    info = m.BackupInfo.model_validate(created.json())
    assert info.verified is True and info.size_bytes > 0
    assert "backup-encryption-key" in h.keychain.values
    status = m.BackupStatus.model_validate(h.get("/backup").json())
    assert [b.id for b in status.backups] == [info.id] and status.encryption_key_present
    assert status.last_backup_at is not None and status.backups[0].verified is True
    unverified = m.BackupInfo.model_validate(h.post("/backup", {"verify": False}).json())
    assert unverified.verified is False and unverified.id != info.id
    h.restart()
    assert len(m.BackupStatus.model_validate(h.get("/backup").json()).backups) == 2
    assert h.keychain.values["backup-encryption-key"] not in h.get("/backup").text


def test_restore_needs_the_users_approval(h: Harness) -> None:
    rule = m.Rule.model_validate(h.post("/rules", {"name": "keep me", "action": "tool_a", "behavior": "ask"}).json())
    backup = m.BackupInfo.model_validate(h.post("/backup", {}).json())
    h.delete(f"/rules/{rule.id}")
    request = {"backup_id": backup.id, "confirm": "restore"}

    first = h.post("/backup/restore", request)
    assert first.status_code == 202
    result = m.BackupRestoreResult.model_validate(first.json())
    assert result.restored is False and result.backup_id == backup.id
    assert all(r.id != rule.id for r in _rules(h))  # a single call restores nothing
    pending = [a for a in m.ApprovalList.model_validate(h.get("/approvals").json()).approvals
               if a.action == "database_restore"]
    assert len(pending) == 1 and pending[0].status == "pending"

    # asking again before the user decided proposes nothing new and restores nothing
    assert h.post("/backup/restore", request).status_code == 202
    assert len([a for a in m.ApprovalList.model_validate(h.get("/approvals").json()).approvals
                if a.action == "database_restore"]) == 1
    assert all(r.id != rule.id for r in _rules(h))

    approved = h.post(f"/approvals/{pending[0].id}/approve", {})
    assert approved.status_code == 200
    done = h.post("/backup/restore", request)
    assert done.status_code == 200
    restored = m.BackupRestoreResult.model_validate(done.json())
    assert restored.restored is True and restored.restart_required is True
    h.restart()
    assert any(r.id == rule.id for r in _rules(h))  # the backup's data is back

    # the approval was one-time: a new restore asks again
    again = h.post("/backup/restore", request)
    assert again.status_code == 202 and m.BackupRestoreResult.model_validate(again.json()).restored is False


def test_a_denied_restore_does_nothing(h: Harness) -> None:
    backup = m.BackupInfo.model_validate(h.post("/backup", {}).json())
    request = {"backup_id": backup.id, "confirm": "restore"}
    assert h.post("/backup/restore", request).status_code == 202
    approval = next(a for a in m.ApprovalList.model_validate(h.get("/approvals").json()).approvals
                    if a.action == "database_restore")
    assert h.post(f"/approvals/{approval.id}/deny", {}).status_code == 200
    assert h.post("/backup/restore", request).status_code == 202  # asks again; nothing was restored


def test_restore_after_a_restart_loses_the_token_and_asks_again(h: Harness) -> None:
    backup = m.BackupInfo.model_validate(h.post("/backup", {}).json())
    request = {"backup_id": backup.id, "confirm": "restore"}
    h.post("/backup/restore", request)
    approval = next(a for a in m.ApprovalList.model_validate(h.get("/approvals").json()).approvals
                    if a.action == "database_restore")
    h.post(f"/approvals/{approval.id}/approve", {})
    h.restart()  # approval tokens live in memory only
    assert h.post("/backup/restore", request).status_code == 202


def test_restore_rejects_bad_requests(h: Harness) -> None:
    assert h.post("/backup/restore", {"backup_id": "missing", "confirm": "restore"}).status_code == 404
    assert h.post("/backup/restore", {"backup_id": "../opendot", "confirm": "restore"}).status_code == 404
    assert h.post("/backup/restore", {"backup_id": "x", "confirm": "yes"}).status_code == 422
    backup = m.BackupInfo.model_validate(h.post("/backup", {}).json())
    h.keychain.values.pop("backup-encryption-key")
    response = h.post("/backup/restore", {"backup_id": backup.id, "confirm": "restore"})
    assert response.status_code == 409
    assert m.ErrorResponse.model_validate(response.json()).code == "backup_key_missing"
