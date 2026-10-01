"""M4 endpoints on the real runtime: version, onboarding, ChatGPT sign-in, companion, conversations,
activity, memory and connections. Bodies are validated against the contract models, state must
survive a new app instance over the same database, and none of these endpoints may answer 501.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from opendot_core.agent.runtime import AgentRuntime, build_agent_runtime
from opendot_core.api.contract import ENDPOINTS
from opendot_core.api.models import (
    ActivityList,
    ChatGPTSignInStart,
    ChatGPTStatus,
    CompanionProfile,
    CompanionTaskList,
    Connection,
    ConnectionDisconnectResult,
    ConnectionList,
    Conversation,
    ConversationList,
    MemoryCorrectResult,
    MemoryForgetResult,
    MemorySearchResult,
    OkResponse,
    OnboardingState,
    VersionResponse,
)
from opendot_core.api.routes.connections import connection_for
from opendot_core.api.serve_cli import build_serve_app
from opendot_core.db import Database
from opendot_core.eval.fake_provider import text_turn, tool_turn
from opendot_core.eval.harness import Stack, build_world
from opendot_core.events import EventStore
from opendot_core.memory_graph import MemoryGraph
from opendot_core.providers.chatgpt_plan import ChatGPTPlanProvider
from opendot_core.providers.registry import ProviderRegistry, ProviderSettings
from opendot_core.scheduled_tasks import ScheduledTaskStore
from opendot_core.settings_store import SettingsStore
from tests.providers.chatgpt_plan_fakes import FakeOpenAI, MemoryTokenStore, complete_browser_leg
from tests.schema_version import LATEST_SCHEMA_VERSION

TOKEN = "m4-endpoints-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
DRAFT = {"to": "bob@example.com", "subject": "Lunch", "body": "Free Friday?"}
OK_REVIEW = '{"verdict": "ok", "reasons": ["Looks fine."]}'


class Env:
    """One daemon: a database, a registry and the real runtime, rebuilt by ``restart``."""

    def __init__(self, tmp_path: Path, script: list, provider=None) -> None:
        self.world: Stack = build_world(tmp_path, script=script)
        self.provider = provider or self.world.provider
        self.build()

    def build(self) -> None:
        registry = ProviderRegistry(ProviderSettings(enabled={"chatgpt_plan"}))
        registry.register(self.provider)
        self.runtime: AgentRuntime = build_agent_runtime(
            Database(self.world.database.path), registry, self.world.tools, api_token=TOKEN,
            actor=self.world.tools.actor, clock=self.world.world.clock,
        )
        self.client = TestClient(self.runtime.app)
        self.hub = self.runtime.app.state.hub
        self.context = self.runtime.app.state.context

    def restart(self) -> None:
        """A new app instance over the same database (and the same provider/keychain)."""
        self.build()

    @property
    def db(self) -> Database:
        return self.runtime.database

    def get(self, path: str, model=None, status: int = 200, **kw):
        return self._check(self.client.get(path, headers=AUTH, **kw), model, status)

    def post(self, path: str, model=None, status: int = 200, json=None):
        return self._check(self.client.post(path, headers=AUTH, json=json if json is not None else {}), model, status)

    @staticmethod
    def _check(response, model, status):
        assert response.status_code == status, response.text
        body = response.json()
        if model is not None and status == 200:
            model.model_validate(body)
        return body

    def chat(self, text: str, conversation_id: str | None = None) -> dict:
        payload = {"text": text, **({"conversation_id": conversation_id} if conversation_id else {})}
        accepted = self.client.post("/v1/chat/messages", headers=AUTH, json=payload)
        assert accepted.status_code == 202
        self.hub.wait_idle(30)
        return accepted.json()


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path, [text_turn("Hello there.")] * 6)


# --- version ---------------------------------------------------------------------------------


def test_version(env: Env) -> None:
    body = env.get("/v1/version", VersionResponse)
    assert body["api_version"] == "v1" and body["schema_version"] == LATEST_SCHEMA_VERSION
    assert body["daemon_version"] and env.client.get("/v1/version").status_code == 401


# --- ChatGPT sign-in and onboarding ----------------------------------------------------------


class SignInEnv:
    def __init__(self, tmp_path: Path) -> None:
        self.fake = FakeOpenAI()
        self.store = MemoryTokenStore()
        self.provider = ChatGPTPlanProvider(
            self.store, tmp_path / "chatgpt_plan.json", http=self.fake.client(), clock=lambda: self.fake.now
        )
        self.env = Env(tmp_path, [text_turn("Hi.")], provider=self.provider)

    def start(self) -> dict:
        started = self.env.post("/v1/auth/chatgpt/start", ChatGPTSignInStart, json={"open_browser": False})
        self.fake.learn_authorize(started["authorize_url"])
        return started

    def browser_leg(self, started: dict) -> None:
        redirect = parse_qs(urlparse(started["authorize_url"]).query)["redirect_uri"][0]
        complete_browser_leg(SimpleNamespace(authorize_url=started["authorize_url"], redirect_uri=redirect))

    def wait(self, *, until: str) -> dict:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status = self.env.get("/v1/auth/chatgpt/status", ChatGPTStatus)
            if status["state"] == until:
                return status
            time.sleep(0.05)
        raise AssertionError(f"sign-in never reached {until}: {status}")

    def sign_in(self) -> dict:
        self.browser_leg(self.start())
        return self.wait(until="signed_in")


@pytest.fixture
def signin(tmp_path: Path):
    s = SignInEnv(tmp_path)
    yield s
    s.provider.cancel_sign_in()


def test_signed_out_then_pending_then_signed_in(signin: SignInEnv) -> None:
    assert signin.env.get("/v1/auth/chatgpt/status", ChatGPTStatus)["state"] == "signed_out"
    started = signin.start()
    assert started["state"] == "pending" and "/oauth/authorize" in started["authorize_url"]
    assert started["authorize_url"].startswith("https://auth.openai.com/")
    assert signin.env.get("/v1/auth/chatgpt/status", ChatGPTStatus)["state"] == "pending"
    again = signin.env.post("/v1/auth/chatgpt/start", ChatGPTSignInStart)
    assert again["authorize_url"] == started["authorize_url"]  # one sign-in at a time
    signin.browser_leg(started)
    status = signin.wait(until="signed_in")
    assert status["eligible"] and status["plan_label"] == "Using ChatGPT plan" and status["credits_enabled"] is None  # unknown is not "off" (security review S6)
    assert status["account_label"] == "sam@example.test" and status["manage_usage_url"].startswith("https://")
    blob = json.dumps(status) + json.dumps(started)
    assert "at-1" not in blob and "rt-1" not in blob and "urn:uuid" not in blob


def test_signed_in_state_survives_a_new_app_instance(signin: SignInEnv) -> None:
    signin.sign_in()
    signin.env.restart()
    assert signin.env.get("/v1/auth/chatgpt/status", ChatGPTStatus)["state"] == "signed_in"


def test_account_without_plan_is_not_eligible(signin: SignInEnv) -> None:
    signin.fake.granted_scope = "openid profile email offline_access"
    signin.browser_leg(signin.start())
    status = signin.wait(until="error")
    assert status["eligible"] is False and status["plan"] == "ineligible"
    assert "Plus or Pro" in status["ineligible_reason"]
    assert signin.fake.revoked == ["rt-1"] and "chatgpt_plan.credentials" not in signin.store.values


def test_denied_sign_in_reports_an_error_and_can_be_retried(signin: SignInEnv) -> None:
    started = signin.start()
    redirect = parse_qs(urlparse(started["authorize_url"]).query)["redirect_uri"][0]
    complete_browser_leg(SimpleNamespace(authorize_url=started["authorize_url"], redirect_uri=redirect),
                         extra="&error=access_denied")
    status = signin.wait(until="error")
    assert status["eligible"] is False and status["error"]
    assert signin.sign_in()["state"] == "signed_in"


def test_paused_plan_is_reported_without_signing_out(signin: SignInEnv) -> None:
    signin.sign_in()
    signin.provider._pause()  # what a usage-limit 429 does
    status = signin.env.get("/v1/auth/chatgpt/status", ChatGPTStatus)
    assert status["state"] == "signed_in" and "Paused" in status["error"]
    signin.provider.resume()
    assert signin.env.get("/v1/auth/chatgpt/status", ChatGPTStatus)["error"] is None


def test_disconnect_revokes_and_signs_out(signin: SignInEnv) -> None:
    signin.sign_in()
    signin.env.post("/v1/auth/chatgpt/disconnect", OkResponse)
    assert signin.fake.revoked == ["rt-1"]
    assert "chatgpt_plan.credentials" not in signin.store.values
    assert signin.env.get("/v1/auth/chatgpt/status", ChatGPTStatus)["state"] == "signed_out"


def test_start_without_a_signin_capable_provider_is_a_clear_error(env: Env) -> None:
    assert env.get("/v1/auth/chatgpt/status", ChatGPTStatus)["state"] == "signed_out"
    refused = env.client.post("/v1/auth/chatgpt/start", headers=AUTH, json={})
    assert refused.status_code == 409 and refused.json()["code"] == "provider_error"


def test_onboarding_walks_through_the_steps_and_persists(signin: SignInEnv) -> None:
    env = signin.env
    state = env.get("/v1/onboarding", OnboardingState)
    assert state["current_step"] == "companion" and state["completed_steps"] == []
    early = env.client.post("/v1/onboarding/complete", headers=AUTH)
    assert early.status_code == 409 and early.json()["code"] == "onboarding_incomplete"

    state = env.post("/v1/onboarding/companion", OnboardingState, json={"name": "Juno", "avatar_seed": "open-fold-7"})
    assert state["companion_name"] == "Juno" and state["avatar_seed"] == "open-fold-7"
    assert state["current_step"] == "chatgpt"
    assert env.get("/v1/companion", CompanionProfile)["name"] == "Juno"

    signin.sign_in()
    state = env.get("/v1/onboarding", OnboardingState)
    assert state["current_step"] == "weekly_limit" and state["chatgpt"]["state"] == "signed_in"

    state = env.post("/v1/onboarding/acknowledge", OnboardingState, json={"weekly_limit_set": True, "credits_off": True})
    assert state["weekly_limit_acknowledged"] and state["credits_off_acknowledged"]
    assert state["current_step"] == "connections"

    env.restart()  # everything above is persisted
    state = env.get("/v1/onboarding", OnboardingState)
    assert state["current_step"] == "connections" and state["companion_name"] == "Juno"

    done = env.post("/v1/onboarding/complete", OnboardingState)
    assert done["current_step"] == "done" and "done" in done["completed_steps"]
    assert "Juno" in done["intro_message"]
    env.restart()
    again = env.get("/v1/onboarding", OnboardingState)
    assert again["current_step"] == "done" and again["intro_message"] == done["intro_message"]


def test_onboarding_acknowledge_refuses_while_credit_use_is_on(signin: SignInEnv) -> None:
    signin.sign_in()
    signin.provider.credits_enabled = True  # a provider that reports credit use as on
    refused = signin.env.client.post(
        "/v1/onboarding/acknowledge", headers=AUTH, json={"weekly_limit_set": True, "credits_off": True}
    )
    assert refused.status_code == 409 and refused.json()["code"] == "credits_enabled"
    assert signin.env.get("/v1/auth/chatgpt/status", ChatGPTStatus)["credits_enabled"] is True


def test_onboarding_rejects_bad_bodies(env: Env) -> None:
    assert env.client.post("/v1/onboarding/companion", headers=AUTH, json={"name": ""}).status_code == 422
    assert env.client.post("/v1/onboarding/acknowledge", headers=AUTH, json={"credits_off": True}).status_code == 422


# --- companion -------------------------------------------------------------------------------


def test_companion_rename_and_avatar_persist(env: Env) -> None:
    assert env.get("/v1/companion", CompanionProfile)["name"] == "OpenDot"
    renamed = env.post("/v1/companion/rename", CompanionProfile, json={"name": "  Juno "})
    assert renamed["name"] == "Juno"
    seeded = env.post("/v1/companion/avatar", CompanionProfile, json={"avatar_seed": "calm-loop-3"})
    assert seeded["avatar_seed"] == "calm-loop-3" and seeded["name"] == "Juno"
    rolled = env.post("/v1/companion/avatar", CompanionProfile)
    assert rolled["avatar_seed"] not in ("calm-loop-3", "default")
    env.restart()
    profile = env.get("/v1/companion", CompanionProfile)
    assert profile["name"] == "Juno" and profile["avatar_seed"] == rolled["avatar_seed"]
    assert env.client.post("/v1/companion/rename", headers=AUTH, json={"name": "   "}).status_code == 422
    assert env.client.post("/v1/companion/rename", headers=AUTH, json={}).status_code == 422
    # pause and resume still answer with the persisted identity
    assert env.post("/v1/companion/pause", CompanionProfile, json={"reason": "stop"})["name"] == "Juno"


def test_companion_tasks_cover_in_progress_scheduled_and_completed(env: Env) -> None:
    env.chat("say hello")
    waiting = env.runtime.loop.start_task("water the plants")  # created, not run: in progress
    with env.db.connect() as connection:
        ScheduledTaskStore.schedule(
            connection, prompt="check the grandstand order", run_at=datetime.now(UTC) + timedelta(hours=3),
            chat_id=1, idempotency_key="k1",
        )
        connection.commit()
    tasks = env.get("/v1/companion/tasks", CompanionTaskList)
    assert [t["id"] for t in tasks["in_progress"]] == [waiting]
    assert [t["title"] for t in tasks["scheduled"]] == ["check the grandstand order"]
    assert tasks["scheduled"][0]["scheduled_for"] and tasks["scheduled"][0]["status"] == "scheduled"
    assert [t["title"] for t in tasks["completed"]] == ["say hello"]
    assert tasks["completed"][0]["completed_at"] and tasks["completed"][0]["credits_used"] > 0
    assert tasks["completed"][0]["summary"] == "Hello there."


def test_companion_reset_needs_confirm_and_never_touches_the_audit_log(env: Env) -> None:
    env.post("/v1/companion/rename", CompanionProfile, json={"name": "Juno"})
    conv = env.chat("say hello")
    graph = MemoryGraph(env.db)
    memory = graph.remember("The user likes tea.")
    env.runtime.loop.start_task("a task still waiting")
    with env.db.connect() as connection:
        ScheduledTaskStore.schedule(
            connection, prompt="later", run_at=datetime.now(UTC) + timedelta(hours=1), chat_id=1, idempotency_key="k2"
        )
        connection.commit()
    with env.db.connect() as connection:
        before = connection.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0]
    assert before > 0

    for bad in ({}, {"confirm": "yes"}, {"confirm": "reset", "forget_memory": "maybe"}):
        assert env.client.post("/v1/companion/reset", headers=AUTH, json=bad).status_code == 422
    assert env.get(f"/v1/chat/conversations/{conv['conversation_id']}", Conversation)

    profile = env.post("/v1/companion/reset", CompanionProfile, json={"confirm": "reset"})
    assert profile["name"] == "Juno"
    assert env.get("/v1/chat/conversations", ConversationList)["conversations"] == []
    tasks = env.get("/v1/companion/tasks", CompanionTaskList)
    assert tasks == {"in_progress": [], "scheduled": [], "completed": []}
    assert graph.get_memory(memory.id).status == "confirmed"  # memory is kept unless asked
    with env.db.connect() as connection:
        after = connection.execute("SELECT COUNT(*) FROM tool_runs").fetchone()[0]
        assert connection.execute("SELECT COUNT(*) FROM tool_runs WHERE tool = 'companion_reset'").fetchone()[0] == 1
    assert after > before
    from opendot_core.audit import AuditLog

    assert AuditLog(env.db).verify()

    env.post("/v1/companion/reset", CompanionProfile, json={"confirm": "reset", "forget_memory": True})
    assert graph.get_memory(memory.id).status == "deleted"


# --- conversations ---------------------------------------------------------------------------


def test_conversations_persist_messages_in_order_with_usage(env: Env) -> None:
    first = env.chat("What's the plan for today?")
    second = env.chat("And tomorrow?", conversation_id=first["conversation_id"])
    assert second["conversation_id"] == first["conversation_id"]

    listing = env.get("/v1/chat/conversations", ConversationList)["conversations"]
    assert len(listing) == 1 and listing[0]["id"] == first["conversation_id"]
    assert listing[0]["message_count"] == 4 and listing[0]["title"] == "What's the plan for today?"

    conversation = env.get(f"/v1/chat/conversations/{first['conversation_id']}", Conversation)
    roles = [m["role"] for m in conversation["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert [m["text"] for m in conversation["messages"]][::2] == ["What's the plan for today?", "And tomorrow?"]
    assistant = conversation["messages"][1]
    assert assistant["text"] == "Hello there." and assistant["usage"]["credits"] > 0
    assert assistant["usage"]["model"] and assistant["usage"]["input_tokens"] > 0

    env.restart()  # a new app instance over the same database
    again = env.get(f"/v1/chat/conversations/{first['conversation_id']}", Conversation)
    assert [m["id"] for m in again["messages"]] == [m["id"] for m in conversation["messages"]]
    assert env.client.get("/v1/chat/conversations/nope", headers=AUTH).status_code == 404


def test_conversation_records_tool_calls_and_the_approval_card(tmp_path: Path) -> None:
    env = Env(tmp_path, [tool_turn("gmail_draft_create", DRAFT), text_turn(OK_REVIEW), text_turn("Draft is ready.")])
    sent = env.chat("draft an email to bob@example.com about lunch")
    conversation = env.get(f"/v1/chat/conversations/{sent['conversation_id']}", Conversation)
    reply = conversation["messages"][-1]
    assert reply["role"] == "assistant" and reply["approval_id"]
    assert [c["name"] for c in reply["tool_calls"]] == ["gmail_draft_create"]
    approval_id = reply["approval_id"]
    env.post(f"/v1/approvals/{approval_id}/approve", json={})
    env.hub.wait_idle(30)
    final = env.get(f"/v1/chat/conversations/{sent['conversation_id']}", Conversation)["messages"][-1]
    assert final["text"] == "Draft is ready." and final["approval_id"] == approval_id and final["usage"]


# --- activity --------------------------------------------------------------------------------


def test_activity_explains_what_happened_and_why(tmp_path: Path) -> None:
    env = Env(
        tmp_path,
        [tool_turn("memory_search", {"query": "tea"}), text_turn("You like tea."),
         tool_turn("gmail_draft_create", DRAFT), text_turn(OK_REVIEW), text_turn("Draft is ready.")],
    )
    memory = MemoryGraph(env.db).remember("The user likes tea.")
    original = env.world.tools.run
    env.world.tools.run = lambda name, args, **kw: (  # type: ignore[method-assign]
        json.dumps({"results": [{"id": memory.id, "statement": memory.statement}]})
        if name == "memory_search" else original(name, args, **kw)
    )
    env.chat("what do I like to drink?")
    env.chat("draft an email to bob@example.com about lunch")

    page = env.get("/v1/activity", ActivityList)
    items = page["items"]
    kinds = {i["kind"] for i in items}
    assert {"message", "action", "approval"} <= kinds
    used = next(i for i in items if i["kind"] == "action" and i["title"].startswith("Used memory search"))
    assert used["rule_id"] and used["rule_name"] and used["why"] and used["memory_ids"] == [memory.id]
    asked = next(i for i in items if i["title"].startswith("Asked before using"))
    assert asked["kind"] == "approval" and asked["approval_id"] and "Looks fine." in asked["reviewer_note"]
    assert asked["rule_id"]
    message = next(i for i in items if i["kind"] == "message" and "what do I like" in i["title"])
    assert message["credits"] > 0
    assert [i["at"] for i in items] == sorted((i["at"] for i in items), reverse=True)


def test_activity_pagination_walks_every_item_once(env: Env) -> None:
    env.chat("one")
    env.chat("two")
    everything = env.get("/v1/activity?limit=200", ActivityList)
    assert everything["next_cursor"] is None and len(everything["items"]) >= 2
    seen, cursor = [], None
    for _ in range(50):
        page = env.get("/v1/activity", ActivityList, params={"limit": 1, **({"cursor": cursor} if cursor else {})})
        assert len(page["items"]) <= 1
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [i["id"] for i in everything["items"]]
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert env.get("/v1/activity", ActivityList, params={"since": future})["items"] == []
    assert env.client.get("/v1/activity?limit=0", headers=AUTH).status_code == 422
    assert env.client.get("/v1/activity?bogus=1", headers=AUTH).status_code == 422


# --- memory ----------------------------------------------------------------------------------


def _seed_memories(env: Env) -> dict[str, str]:
    graph = MemoryGraph(env.db)
    ids: dict[str, str] = {}
    when = datetime(2026, 3, 1, tzinfo=UTC)
    with env.db.connect() as connection:
        mail = EventStore.append(connection, source="gmail", external_id="m1", occurred_at=when,
                                 content="lunch", metadata={}).id
        cal = EventStore.append(connection, source="google_calendar", external_id="c1", occurred_at=when,
                                content="standup", metadata={}).id
        connection.commit()
    ids["tea"] = graph.remember("The user likes green tea.").id
    ids["lunch"] = graph.remember("Lunch with Robin is on Friday.", source_event_id=mail, confirmed=False,
                                  status="candidate").id
    ids["standup"] = graph.remember("Standup is at 9 on weekdays.", source_event_id=cal).id
    graph.create_entity(entity_type="person", label="Robin Park", source_event_id=mail)
    return ids


def test_memory_search_filters_and_totals(env: Env) -> None:
    ids = _seed_memories(env)
    everything = env.get("/v1/memory", MemorySearchResult)
    assert everything["total"] == 3
    assert {i["id"] for i in everything["items"]} == set(ids.values())
    tea = env.get("/v1/memory?q=tea", MemorySearchResult)
    assert [i["id"] for i in tea["items"]] == [ids["tea"]]
    assert tea["items"][0]["source"] == "user" and tea["items"][0]["confidence"] == "confirmed"
    lunch = env.get("/v1/memory?source=gmail", MemorySearchResult)["items"]
    assert [i["id"] for i in lunch] == [ids["lunch"]]
    assert lunch[0]["confidence"] == "inferred" and lunch[0]["people"] == ["Robin Park"]
    assert lunch[0]["source_label"] == "Gmail"
    robin = env.get("/v1/memory?person=robin park", MemorySearchResult)["items"]
    assert [i["id"] for i in robin] == [ids["lunch"]]
    limited = env.get("/v1/memory?limit=1", MemorySearchResult)
    assert len(limited["items"]) == 1 and limited["total"] == 3
    assert env.get("/v1/memory?q=zebra", MemorySearchResult) == {"items": [], "total": 0}
    assert env.client.get("/v1/memory?limit=500", headers=AUTH).status_code == 422


def test_memory_correct_supersedes_without_rewriting_history(env: Env) -> None:
    ids = _seed_memories(env)
    result = env.post(f"/v1/memory/{ids['tea']}/correct", MemoryCorrectResult, json={"statement": "The user likes black tea."})
    assert result["superseded_id"] == ids["tea"] and result["corrected"]["statement"] == "The user likes black tea."
    assert result["corrected"]["id"] != ids["tea"] and result["corrected"]["superseded"] is False
    old = MemoryGraph(env.db).get_memory(ids["tea"])
    assert old.status == "superseded" and old.statement == "The user likes green tea."  # history kept
    with env.db.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_history WHERE memory_id = ? AND next_status = 'superseded'", (ids["tea"],)
        ).fetchone()[0] == 1
    found = env.get("/v1/memory?q=tea", MemorySearchResult)["items"]
    assert {i["statement"]: i["superseded"] for i in found} == {
        "The user likes green tea.": True, "The user likes black tea.": False}
    again = env.client.post(f"/v1/memory/{ids['tea']}/correct", headers=AUTH, json={"statement": "x"})
    assert again.status_code == 409
    assert env.client.post("/v1/memory/nope/correct", headers=AUTH, json={"statement": "x"}).status_code == 404
    assert env.client.post(f"/v1/memory/{ids['tea']}/correct", headers=AUTH, json={}).status_code == 422


def test_memory_forget_by_item_source_time_and_person(env: Env) -> None:
    ids = _seed_memories(env)
    one = env.post("/v1/memory/forget", MemoryForgetResult, json={"scope": "item", "item_id": ids["tea"]})
    assert one == {"forgotten_count": 1, "scope": "item"}
    assert MemoryGraph(env.db).get_memory(ids["tea"]).status == "deleted"
    assert env.get("/v1/memory?q=tea", MemorySearchResult)["total"] == 0
    assert env.client.post("/v1/memory/forget", headers=AUTH, json={"scope": "item", "item_id": ids["tea"]}).status_code == 404

    person = env.post("/v1/memory/forget", MemoryForgetResult, json={"scope": "person", "person": "Robin Park"})
    assert person["forgotten_count"] == 1 and MemoryGraph(env.db).get_memory(ids["lunch"]).status == "deleted"

    source = env.post("/v1/memory/forget", MemoryForgetResult, json={"scope": "source", "source": "google_calendar"})
    assert source["forgotten_count"] == 1 and MemoryGraph(env.db).get_memory(ids["standup"]).status == "deleted"

    fresh = MemoryGraph(env.db).remember("Something new.")
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    none = env.post("/v1/memory/forget", MemoryForgetResult, json={"scope": "time", "since": future})
    assert none["forgotten_count"] == 0 and MemoryGraph(env.db).get_memory(fresh.id).status == "confirmed"
    window = env.post("/v1/memory/forget", MemoryForgetResult, json={"scope": "time", "since": past})
    assert window["forgotten_count"] == 1 and MemoryGraph(env.db).get_memory(fresh.id).status == "deleted"
    with env.db.connect() as connection:  # forgetting is on the record
        assert connection.execute("SELECT COUNT(*) FROM tool_runs WHERE tool = 'memory_forget'").fetchone()[0] == 4


def test_memory_forget_needs_what_its_scope_needs(env: Env) -> None:
    for body in ({"scope": "item"}, {"scope": "source"}, {"scope": "person"}, {"scope": "time"}, {"scope": "topic"}):
        assert env.client.post("/v1/memory/forget", headers=AUTH, json=body).status_code == 422


# --- connections -----------------------------------------------------------------------------


def _sync_row(env: Env, connector: str, *, ok: datetime | None, error: str | None = None, account: str = "self") -> None:
    now = datetime.now(UTC).isoformat()
    with env.db.connect() as connection:
        connection.execute(
            "INSERT INTO sync_state (connector, account, cursor, last_success_at, last_error, updated_at) "
            "VALUES (?, ?, NULL, ?, ?, ?)",
            (connector, account, ok.isoformat() if ok else None, error, now),
        )
        connection.commit()


def test_connections_list_reports_health(env: Env) -> None:
    assert env.get("/v1/connections", ConnectionList)["connections"] == []
    now = datetime.now(UTC)
    _sync_row(env, "gmail", ok=now - timedelta(minutes=5), account="sam@example.test")
    _sync_row(env, "google_calendar", ok=now - timedelta(days=3))
    _sync_row(env, "github", ok=None, error="HTTPError")
    _sync_row(env, "telegram", ok=now)  # not an app connection
    SettingsStore(env.db).set("google_connection", {"write_opt_in": True, "granted_scopes": ["gmail.compose"]})
    items = {c["id"]: c for c in env.get("/v1/connections", ConnectionList)["connections"]}
    assert set(items) == {"gmail", "google_calendar", "github"}
    assert items["gmail"]["health"] == "ok" and items["gmail"]["account_label"] == "sam@example.test"
    assert items["gmail"]["write_opt_in"] is True and items["gmail"]["read_only"] is False
    assert items["google_calendar"]["health"] == "stale" and items["google_calendar"]["write_opt_in"] is True
    assert items["github"]["health"] == "error" and "HTTPError" in items["github"]["health_detail"]
    assert items["github"]["write_opt_in"] is False and items["github"]["account_label"] == "GitHub"
    assert items["github"]["last_synced_at"] is None


def test_connections_include_a_google_app_that_never_synced(env: Env) -> None:
    SettingsStore(env.db).set("google_connection", {"granted_scopes": ["https://www.googleapis.com/auth/calendar.events"]})
    items = env.get("/v1/connections", ConnectionList)["connections"]
    assert [(c["id"], c["health"]) for c in items] == [("google_calendar", "never_synced")]


def test_write_opt_in_available_follows_the_google_client_id(env: Env) -> None:
    _sync_row(env, "gmail", ok=datetime.now(UTC))
    _sync_row(env, "github", ok=datetime.now(UTC))

    class Store:
        value: str | None = None

        def get_optional(self, name: str) -> str | None:
            assert name == "google-oauth-client-id"
            return self.value

    store = Store()
    env.context.extras["secret_store"] = store
    assert connection_for(env.context, "gmail").write_opt_in_available is False
    store.value = "client-123"
    assert connection_for(env.context, "gmail").write_opt_in_available is True
    assert connection_for(env.context, "github").write_opt_in_available is False  # Google apps only
    assert isinstance(connection_for(env.context, "google_calendar"), Connection)  # never synced is still a Connection


def test_connection_sync_runs_the_registered_syncer(env: Env) -> None:
    _sync_row(env, "github", ok=datetime.now(UTC) - timedelta(days=3))
    unavailable = env.client.post("/v1/connections/github/sync", headers=AUTH)
    assert unavailable.status_code == 409 and unavailable.json()["code"] == "sync_unavailable"
    calls: list[str] = []

    def sync_github() -> None:
        calls.append("github")
        with env.db.connect() as connection:
            connection.execute("UPDATE sync_state SET last_success_at = ?, last_error = NULL WHERE connector = 'github'",
                               (datetime.now(UTC).isoformat(),))
            connection.commit()

    env.context.extras["connector_syncers"] = {"github": sync_github}
    result = env.post("/v1/connections/github/sync", Connection)
    assert calls == ["github"] and result["health"] == "ok" and result["id"] == "github"
    assert env.client.post("/v1/connections/gmail/sync", headers=AUTH).status_code == 404
    assert env.client.post("/v1/connections/nope/sync", headers=AUTH).status_code == 404


def test_connection_sync_failure_shows_as_error_health(env: Env) -> None:
    _sync_row(env, "github", ok=datetime.now(UTC))

    def broken() -> None:
        raise RuntimeError("boom")

    env.context.extras["connector_syncers"] = {"github": broken}
    result = env.post("/v1/connections/github/sync", Connection)
    assert result["health"] == "error" and "RuntimeError" in result["health_detail"]


def test_disconnect_can_forget_what_was_learned_from_the_app(env: Env) -> None:
    ids = _seed_memories(env)
    _sync_row(env, "gmail", ok=datetime.now(UTC))
    _sync_row(env, "google_calendar", ok=datetime.now(UTC))
    revoked: list[str] = []
    env.context.extras["connector_disconnectors"] = {"gmail": lambda: revoked.append("gmail")}
    listed = {c["id"]: c for c in env.get("/v1/connections", ConnectionList)["connections"]}
    assert listed["gmail"]["memory_item_count"] == 1

    keep = env.post("/v1/connections/google_calendar/disconnect", ConnectionDisconnectResult, json={})
    assert keep == {"disconnected": True, "forgotten_count": 0}
    assert MemoryGraph(env.db).get_memory(ids["standup"]).status == "confirmed"  # kept

    gone = env.post("/v1/connections/gmail/disconnect", ConnectionDisconnectResult, json={"forget_learned": True})
    assert gone == {"disconnected": True, "forgotten_count": 1} and revoked == ["gmail"]
    assert MemoryGraph(env.db).get_memory(ids["lunch"]).status == "deleted"
    assert MemoryGraph(env.db).get_memory(ids["tea"]).status == "confirmed"  # not from this app
    assert env.get("/v1/connections", ConnectionList)["connections"] == []
    with env.db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM tool_runs WHERE tool = 'connection_disconnect'").fetchone()[0] == 2
    assert env.client.post("/v1/connections/gmail/disconnect", headers=AUTH, json={}).status_code == 404


# --- no 501 ----------------------------------------------------------------------------------

_MINE = (
    "version onboarding_get onboarding_companion onboarding_acknowledge onboarding_complete chatgpt_start "
    "chatgpt_status chatgpt_disconnect chat_conversations chat_conversation companion_tasks companion_reset "
    "companion_rename companion_avatar activity_list memory_search memory_correct memory_forget connections_list "
    "connection_sync connection_disconnect"
).split()


def test_every_endpoint_in_these_areas_is_served_not_501(tmp_path: Path) -> None:
    from opendot_core.eval.fake_provider import ScriptedProvider

    registry = ProviderRegistry(ProviderSettings(enabled={"chatgpt_plan"}))
    registry.register(ScriptedProvider([]))
    app, _ = build_serve_app(Database(tmp_path / "opendot.db"), token=TOKEN, ui_dist=None, registry=registry)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    by_name = {e.name: e for e in ENDPOINTS}
    assert set(_MINE) <= set(by_name)
    for name in _MINE:
        endpoint = by_name[name]
        path = endpoint.path.replace("{conversation_id}", "c1").replace("{memory_id}", "m1").replace(
            "{connection_id}", "gmail")
        response = client.request(endpoint.method, path, headers=AUTH, json={} if endpoint.method == "POST" else None)
        assert response.status_code != 501, f"{name} still answers 501"
        assert response.status_code in (200, 404, 409, 422), (name, response.status_code, response.text)
