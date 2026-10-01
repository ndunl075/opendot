"""Deterministic fake data generated from the contract models. No real personal data."""

from __future__ import annotations

import types
import zlib
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

BASE_TIME = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)

STR_HINTS: dict[str, str] = {
    "name": "Juniper",
    "companion_name": "Juniper",
    "avatar_seed": "juniper-7",
    "account_label": "demo.user@example.com",
    "plan_label": "ChatGPT Plus",
    "manage_usage_url": "https://chatgpt.com/settings/usage",
    "authorize_url": "https://auth.example.com/authorize?state=demo-state",
    "timezone": "America/New_York",
    "start": "22:00",
    "end": "07:00",
    "daemon_version": "0.2.0",
    "api_version": "v1",
    "build": "mock",
    "model": "mock-model-terra",
    "q": "coffee",
    "stream_path": "/v1/chat/stream",
    "text": "Here is a short demo reply from the mock server.",
    "statement": "Prefers morning meetings before 11am.",
    "source_label": "Gmail",
    "title": "Draft a reply to the venue",
    "summary": "Waiting on the venue to confirm the date.",
    "why": "You asked for a reply to be drafted, and your rules ask before creating drafts.",
    "reviewer_note": "Checked recipient and dates against your calendar.",
    "preview": "To: venue@example.com | Subject: Booking for March 3",
    "action": "gmail.create_draft",
    "job_type": "chat",
    "reason": "Demo reason text.",
    "resume_at": "2026-01-15T14:00:00Z",
}
STR_SUFFIX_HINTS: tuple[tuple[str, str], ...] = (("_id", "{stem}_demo"),)

PAYLOAD = {"to": "venue@example.com", "subject": "Booking for March 3", "body": "Hi, is the hall free on March 3?"}


def _crc(text: str) -> int:
    return zlib.crc32(text.encode())


def _strip(tp: Any) -> Any:
    while get_origin(tp) is Annotated:
        tp = get_args(tp)[0]
    return tp


def _fake_value(tp: Any, name: str, idx: int, default: Any = PydanticUndefined) -> Any:
    tp = _strip(tp)
    origin = get_origin(tp)
    if origin is Literal:
        values = get_args(tp)
        return values[idx % len(values)]
    if origin in (Union, types.UnionType):
        members = [a for a in get_args(tp) if a is not type(None)]
        return _fake_value(members[idx % len(members)], name, idx, default)
    if origin is list:
        (item,) = get_args(tp)
        count = 2
        return [_fake_value(item, name, i) for i in range(count)]
    if origin is dict:
        key_tp, value_tp = get_args(tp)
        if get_origin(_strip(key_tp)) is Literal:
            return {key: _fake_value(value_tp, name, i) for i, key in enumerate(get_args(_strip(key_tp))[:2])}
        return dict(PAYLOAD)
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return fake_model(tp, idx)
    if tp is bool:
        return default if isinstance(default, bool) else True
    if tp is int:
        return 3 + (_crc(name) % 50) if not name.endswith("count") else 2 + idx
    if tp is float:
        return round(1 + (_crc(name) % 400) / 100 + idx * 0.25, 2)
    if tp is datetime:
        return BASE_TIME - timedelta(hours=idx * 5 + _crc(name) % 40)
    if tp is str:
        if name in STR_HINTS:
            return STR_HINTS[name]
        for suffix, template in STR_SUFFIX_HINTS:
            if name.endswith(suffix):
                return template.format(stem=name[: -len(suffix)]) + (f"_{idx + 1}" if suffix == "_id" else "")
        if name == "id":
            return f"id_{idx + 1:03d}"
        return f"{name.replace('_', ' ')} {idx + 1}"
    raise TypeError(f"no fake generator for {tp!r} ({name})")


def fake_model(model: type[BaseModel], idx: int = 0) -> BaseModel:
    """Build a populated, valid instance of ``model``."""
    data: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        data[name] = _fake_value(field.annotation, name, idx, field.default)
    data = _fixup(model.__name__, data, idx)
    return model.model_validate(data)


def _dt(base: Any, **delta: float) -> Any:
    return base + timedelta(**delta)


def _fixup(model_name: str, data: dict[str, Any], idx: int) -> dict[str, Any]:
    """Make generated values tell a coherent story."""
    if model_name == "ChatGPTStatus":
        data.update(state="signed_in", plan="eligible_plus", eligible=True, ineligible_reason=None, error=None,
                    plan_label="Using ChatGPT plan (Plus)", credits_enabled=False)
    elif model_name == "ChatGPTSignInStart":
        data.update(state="pending")
    elif model_name == "OnboardingState":
        data.update(current_step="connections", completed_steps=["companion", "chatgpt", "weekly_limit"],
                    weekly_limit_acknowledged=True, credits_off_acknowledged=True)
    elif model_name == "HealthResponse":
        data.update(status="ok", database_ok=True, provider_ok=True, paused=False)
    elif model_name == "CompanionProfile":
        data.update(paused=False, paused_reason=None, style_preset="warm")
    elif model_name == "CompanionTaskList":
        for key, status in (("in_progress", "in_progress"), ("scheduled", "scheduled"), ("completed", "completed")):
            for i, task in enumerate(data[key]):
                task.status = status
                task.scheduled_for = _dt(BASE_TIME, hours=4 + i) if status == "scheduled" else None
                task.completed_at = _dt(BASE_TIME, hours=-2 - i) if status == "completed" else None
                task.title = {"in_progress": "Draft the venue reply", "scheduled": "Morning brief",
                              "completed": "Set a dentist reminder"}[status] + ("" if i == 0 else " (follow-up)")
    elif model_name == "CompanionTask":
        data.update(status="in_progress", scheduled_for=None, completed_at=None)
    elif model_name == "ChatMessage":
        data.update(role="assistant" if idx else "user", tool_calls=[], approval_id=None,
                    usage=None if not idx else data["usage"])
        if idx == 0:
            data["text"] = "Can you draft a reply to the venue about March 3?"
    elif model_name == "ToolCallRecord":
        data.update(name="gmail.create_draft", summary="Create a Gmail draft to venue@example.com", status="ok")
    elif model_name == "UsageStamp":
        data.update(effort="low", credits=0.12)
    elif model_name == "ApprovalItem":
        data.update(status="pending", payload=dict(PAYLOAD), decided_at=None,
                    rule_suggestion="Always allow drafts to venue@example.com")
    elif model_name == "ApprovalDecisionResult":
        data.update(executed=True, result_summary="Draft created in Gmail.")
    elif model_name == "ActivityItem":
        data.update(kind=("action", "approval", "reminder", "sync")[idx % 4], rule_id="rule_002",
                    rule_name="Ask before drafting email")
    elif model_name == "Rule":
        data.update(enabled=True, locked=False, core_deny=False)
    elif model_name == "RuleList":
        names = [
            ("Ask before drafting email", "gmail.create_draft", "ask", False),
            ("Create calendar events from my own invites", "calendar.create_event", "auto_if_preapproved", False),
            ("Read my inbox", "gmail.read", "auto", False),
            ("Hand off purchases to me", "purchase.*", "handoff", False),
            ("Never send money", "payments.transfer", "ask", True),
        ]
        data["rules"] = [
            _rule(i, label, action, behavior, core) for i, (label, action, behavior, core) in enumerate(names)
        ]
    elif model_name == "MemoryItem":
        data.update(source="gmail", confidence="confirmed" if idx == 0 else "inferred", superseded=False,
                    people=["Sam Rivera"] if idx == 0 else [])
    elif model_name == "MemorySearchResult":
        data.update(total=len(data["items"]))
    elif model_name == "Connection":
        apps = ("gmail", "google_calendar", "github")
        healths = ("ok", "stale", "error", "never_synced")
        app = apps[idx % 3]
        health = healths[idx % 4]
        data.update(app=app, health=health, account_label="demo.user@example.com" if app != "github" else "demo-user",
                    read_only=True, write_opt_in=False, write_opt_in_available=app != "github",
                    last_synced_at=None if health == "never_synced" else data["last_synced_at"],
                    health_detail={"ok": None, "stale": "Last sync was more than a day ago.",
                                   "error": "Google asked you to sign in again.", "never_synced": None}[health],
                    memory_item_count=0 if health == "never_synced" else 12 + idx)
    elif model_name == "ConnectionList":
        data["connections"] = [_conn(i) for i in range(4)]
    elif model_name == "UsageSummary":
        days = [
            {"day": (BASE_TIME - timedelta(days=i)).date().isoformat(), "credits": round(0.4 + 0.15 * i, 2)}
            for i in range(6, -1, -1)
        ]
        total = round(sum(d["credits"] for d in days), 2)
        data.update(days=7, by_day=days, total_credits=total, today_credits=days[-1]["credits"],
                    daily_budget_remaining=round(5.0 - days[-1]["credits"], 2), paused_for_budget=False,
                    by_task=[{"task_id": "task_001", "title": "Draft the venue reply", "credits": 0.9},
                             {"task_id": "task_002", "title": "Morning brief", "credits": 0.6}],
                    by_job_type=[{"job_type": "chat", "credits": 1.4}, {"job_type": "routine", "credits": 0.6}])
    elif model_name == "UsageBudgets":
        data.update(daily_credits=5.0, task_credits=1.5, daily_hard_stop=True)
    elif model_name in ("KeepAwakeSettings",):
        data.update(enabled=True, only_while_plugged_in=True)
    elif model_name == "QuietHoursSettings":
        data.update(enabled=True)
    elif model_name == "Settings":
        data.update(providers=[
            {"provider": "chatgpt_plan", "label": "ChatGPT plan", "enabled": True, "cost_warning": None,
             "configured": True, "feature_switches": []},
            {"provider": "openai_key", "label": "OpenAI API key", "enabled": False,
             "cost_warning": "Billed per token to your OpenAI account, not your ChatGPT plan.",
             "configured": False, "feature_switches": ["embeddings"]},
            {"provider": "local", "label": "Local model", "enabled": False, "cost_warning": None,
             "configured": False, "feature_switches": []},
        ], tier_overrides=[{"job_type": "routine", "tier": "luna", "effort": "low"}],
            auto_top_tier=False, style_preset="warm")
    elif model_name == "FeatureSwitchState":
        data.update(enabled=False, available=False, **_feature(idx))
    elif model_name == "FeatureSwitchList":
        data["features"] = [_feature_state(i) for i in range(3)]
    elif model_name == "ProviderSpend":
        data.update(_spend(data["provider"]))
    elif model_name == "ProviderSpendList":
        data["providers"] = [_spend(p) for p in ("openai_key", "anthropic_key", "openrouter")]
    elif model_name == "ProviderKeyStatus":
        data.update(key_saved=True)
    elif model_name == "BackupStatus":
        data.update(encryption_key_present=True, last_backup_at=data["backups"][0].created_at)
    elif model_name == "BackupInfo":
        data.update(verified=True, size_bytes=2_400_000 + idx * 1000)
    elif model_name == "BackupRestoreResult":
        data.update(restored=True, restart_required=True)
    elif model_name == "MemoryForgetResult":
        data.update(scope="source", forgotten_count=4)
    elif model_name == "ConnectionDisconnectResult":
        data.update(disconnected=True, forgotten_count=0)
    elif model_name == "ProviderOptIn":
        data.update(enabled=False)
    elif model_name == "CompletedEvent":
        data.update(type="completed")
    elif model_name == "OkResponse":
        data.update(ok=True, message=None)
    return data


_FEATURE_FAKES = (
    ("paid_fallback_when_plan_runs_out", "Use a paid model when the plan runs out",
     "When your plan runs out, OpenDot offers to retry with a paid provider you choose. You approve each retry "
     "and it costs real money per token, up to that provider's monthly cap.",
     ["openai_key", "anthropic_key", "openrouter"], "Needs at least one paid provider turned on."),
    ("claude_as_reviewer", "Use Claude as the reviewer",
     "Risky actions are reviewed by Claude through your Anthropic API key and cost money per token.",
     ["anthropic_key"], "Needs the Anthropic API key provider turned on."),
    ("smarter_memory_search", "Smarter memory search",
     "Memory search queries are sent to the provider you turned on. Key-based providers cost money per token.",
     ["local", "openai_key", "anthropic_key", "openrouter"], "Needs a local model or a paid provider turned on."),
)


def _feature(idx: int) -> dict[str, Any]:
    name, title, warning, requires, requirement = _FEATURE_FAKES[idx % 3]
    return {"name": name, "title": title, "cost_warning": warning, "requires_any_of": requires,
            "requirement": requirement}


def _feature_state(i: int) -> dict[str, Any]:
    return {"enabled": False, "available": False, **_feature(i)}


def _spend(provider: str) -> dict[str, Any]:
    return {"provider": provider, "cap_usd": 20.0, "spent_usd": 3.47, "month": BASE_TIME.strftime("%Y-%m"),
            "cap_set": True}


def _rule(i: int, name: str, action: str, behavior: str, core: bool) -> dict[str, Any]:
    return {
        "id": f"rule_{i + 1:03d}", "name": name, "action": action, "behavior": behavior, "enabled": True,
        "locked": core, "core_deny": core,
        "description": "Core safety rule. It cannot be changed." if core else None,
        "created_at": BASE_TIME - timedelta(days=10 - i), "created_from_approval_id": None,
    }


def _conn(i: int) -> dict[str, Any]:
    return fake_model(_connection_model(), i).model_dump(mode="python")


def _connection_model() -> type[BaseModel]:
    from .models import Connection

    return Connection


def fake_json(model: type[BaseModel], idx: int = 0) -> dict[str, Any]:
    return fake_model(model, idx).model_dump(mode="json")
