"""The one list that drives the schema exporter, the mock server and the contract tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from . import models as m
from .events import CHAT_STREAM_PATH, CLIENT_FRAME_MODELS, EVENT_MODELS

RequestIn = Literal["body", "query"]


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: str
    path: str
    request: type[BaseModel] | None
    response: type[BaseModel]
    request_in: RequestIn | None = None
    summary: str = ""

    def __post_init__(self) -> None:
        if (self.request is None) != (self.request_in is None):
            raise ValueError(f"{self.name}: request and request_in must both be set or both be None")


def _e(
    name: str,
    method: str,
    path: str,
    request: type[BaseModel] | None,
    response: type[BaseModel],
    summary: str,
    request_in: RequestIn | None = "body",
) -> Endpoint:
    return Endpoint(name, method, path, request, response, request_in if request else None, summary)


P = "/v1"

ENDPOINTS: tuple[Endpoint, ...] = (
    # health and version
    _e("health", "GET", f"{P}/health", None, m.HealthResponse, "Daemon health"),
    _e("version", "GET", f"{P}/version", None, m.VersionResponse, "Daemon and API version"),
    # onboarding and ChatGPT sign-in
    _e("onboarding_get", "GET", f"{P}/onboarding", None, m.OnboardingState, "Current onboarding state"),
    _e("onboarding_companion", "POST", f"{P}/onboarding/companion", m.CompanionSetupRequest, m.OnboardingState,
       "Name the companion and pick an avatar seed"),
    _e("onboarding_acknowledge", "POST", f"{P}/onboarding/acknowledge", m.OnboardingAcknowledgeRequest,
       m.OnboardingState, "Confirm the weekly limit is set and credit use is off"),
    _e("onboarding_complete", "POST", f"{P}/onboarding/complete", None, m.OnboardingState,
       "Finish onboarding; the companion introduces itself"),
    _e("chatgpt_start", "POST", f"{P}/auth/chatgpt/start", m.ChatGPTSignInStartRequest, m.ChatGPTSignInStart,
       "Start Sign in with ChatGPT"),
    _e("chatgpt_status", "GET", f"{P}/auth/chatgpt/status", None, m.ChatGPTStatus,
       "Sign-in state and plan eligibility (Plus or Pro)"),
    _e("chatgpt_disconnect", "POST", f"{P}/auth/chatgpt/disconnect", None, m.OkResponse, "Sign out of ChatGPT"),
    # chat
    _e("chat_send", "POST", f"{P}/chat/messages", m.ChatSendRequest, m.ChatSendAccepted,
       "Send a message; replies stream on the chat WebSocket"),
    _e("chat_conversations", "GET", f"{P}/chat/conversations", None, m.ConversationList, "List conversations"),
    _e("chat_conversation", "GET", f"{P}/chat/conversations/{{conversation_id}}", None, m.Conversation,
       "One conversation with its messages"),
    # approvals
    _e("approvals_list", "GET", f"{P}/approvals", None, m.ApprovalList, "Pending and recent approvals"),
    _e("approval_get", "GET", f"{P}/approvals/{{approval_id}}", None, m.ApprovalItem, "One approval"),
    _e("approval_approve", "POST", f"{P}/approvals/{{approval_id}}/approve", m.ApprovalApproveRequest,
       m.ApprovalDecisionResult, "Approve as previewed"),
    _e("approval_edit", "POST", f"{P}/approvals/{{approval_id}}/edit", m.ApprovalEditRequest,
       m.ApprovalDecisionResult, "Edit the payload, then approve"),
    _e("approval_deny", "POST", f"{P}/approvals/{{approval_id}}/deny", m.ApprovalDenyRequest,
       m.ApprovalDecisionResult, "Deny"),
    _e("approval_always_allow", "POST", f"{P}/approvals/{{approval_id}}/always-allow", m.ApprovalAlwaysAllowRequest,
       m.ApprovalDecisionResult, "Approve and create a rule for this action"),
    # companion profile
    _e("companion_get", "GET", f"{P}/companion", None, m.CompanionProfile, "Companion profile"),
    _e("companion_tasks", "GET", f"{P}/companion/tasks", None, m.CompanionTaskList,
       "In progress, scheduled and completed tasks"),
    _e("companion_pause", "POST", f"{P}/companion/pause", m.CompanionPauseRequest, m.CompanionProfile,
       "Pause the companion"),
    _e("companion_resume", "POST", f"{P}/companion/resume", None, m.CompanionProfile, "Resume the companion"),
    _e("plan_limit_resume", "POST", f"{P}/companion/resume-plan-limit", None, m.PlanLimitResumeResult,
       "The user raised their ChatGPT limit (or it reset): clear the plan-limit pause"),
    _e("task_continue", "POST", f"{P}/tasks/{{task_id}}/continue", None, m.TaskActionResult,
       "Continue anyway: let a task that hit its credit budget go on"),
    _e("task_approve_top_tier", "POST", f"{P}/tasks/{{task_id}}/approve-top-tier", None, m.TaskActionResult,
       "Allow this task to use the top-tier model"),
    _e("companion_reset", "POST", f"{P}/companion/reset", m.CompanionResetRequest, m.CompanionProfile,
       "Reset the companion"),
    _e("companion_rename", "POST", f"{P}/companion/rename", m.CompanionRenameRequest, m.CompanionProfile,
       "Rename the companion"),
    _e("companion_avatar", "POST", f"{P}/companion/avatar", m.CompanionAvatarRequest, m.CompanionProfile,
       "Set or re-roll the avatar seed"),
    # activity
    _e("activity_list", "GET", f"{P}/activity", m.ActivityQuery, m.ActivityList, "Activity timeline", "query"),
    # rules
    _e("rules_list", "GET", f"{P}/rules", None, m.RuleList, "Rules, including locked core deny-list items"),
    _e("rule_create", "POST", f"{P}/rules", m.RuleCreateRequest, m.Rule, "Create a rule"),
    _e("rule_update", "PUT", f"{P}/rules/{{rule_id}}", m.RuleUpdateRequest, m.Rule, "Update a rule"),
    _e("rule_delete", "DELETE", f"{P}/rules/{{rule_id}}", None, m.OkResponse, "Delete a rule"),
    # memory
    _e("memory_search", "GET", f"{P}/memory", m.MemorySearchQuery, m.MemorySearchResult, "Search memory", "query"),
    _e("memory_correct", "POST", f"{P}/memory/{{memory_id}}/correct", m.MemoryCorrectRequest,
       m.MemoryCorrectResult, "Correct a memory"),
    _e("memory_forget", "POST", f"{P}/memory/forget", m.MemoryForgetRequest, m.MemoryForgetResult,
       "Forget by item, source, time or person"),
    # connections
    _e("connections_list", "GET", f"{P}/connections", None, m.ConnectionList, "App accounts and their health"),
    _e("connection_start", "POST", f"{P}/connections/start", m.ConnectionStartRequest, m.ConnectionStart,
       "Begin connecting an app"),
    _e("connection_write_opt_in", "PUT", f"{P}/connections/{{connection_id}}/write-opt-in",
       m.ConnectionWriteOptInRequest, m.Connection, "Switch Gmail drafts or Calendar events on or off"),
    _e("connection_sync", "POST", f"{P}/connections/{{connection_id}}/sync", None, m.Connection, "Sync now"),
    _e("connection_disconnect", "POST", f"{P}/connections/{{connection_id}}/disconnect",
       m.ConnectionDisconnectRequest, m.ConnectionDisconnectResult, "Disconnect, optionally forgetting what was learned"),
    # usage
    _e("usage_get", "GET", f"{P}/usage", m.UsageQuery, m.UsageSummary, "Credits by task, day and job type", "query"),
    _e("usage_budgets_get", "GET", f"{P}/usage/budgets", None, m.UsageBudgets, "Budgets"),
    _e("usage_budgets_set", "PUT", f"{P}/usage/budgets", m.UsageBudgets, m.UsageBudgets, "Set budgets"),
    # settings
    _e("settings_get", "GET", f"{P}/settings", None, m.Settings, "All settings"),
    _e("settings_update", "PUT", f"{P}/settings", m.SettingsUpdateRequest, m.Settings, "Update settings"),
    # opt-in providers: enablement, API keys, feature switches, monthly spend caps
    _e("provider_enabled_set", "PUT", f"{P}/providers/{{provider}}/enabled", m.ProviderEnabledRequest,
       m.ProviderOptIn, "Enable or disable an opt-in provider (saving a key never does this)"),
    _e("provider_api_key_save", "PUT", f"{P}/providers/{{provider}}/api-key", m.ProviderApiKeyRequest,
       m.ProviderKeyStatus, "Save an API key to the keychain; the key is never echoed back"),
    _e("provider_api_key_remove", "DELETE", f"{P}/providers/{{provider}}/api-key", None, m.ProviderKeyStatus,
       "Remove a saved API key"),
    _e("features_list", "GET", f"{P}/providers/features", None, m.FeatureSwitchList,
       "Feature switches with cost warnings and requirements"),
    _e("feature_set", "PUT", f"{P}/providers/features/{{feature}}", m.FeatureSwitchSetRequest, m.FeatureSwitchState,
       "Turn a feature switch on or off"),
    _e("spend_get", "GET", f"{P}/providers/spend", None, m.ProviderSpendList,
       "Monthly spend cap and month-to-date spend per paid provider"),
    _e("spend_cap_set", "PUT", f"{P}/providers/{{provider}}/spend-cap", m.ProviderSpendCapRequest, m.ProviderSpend,
       "Set a paid provider's monthly spend cap in USD"),
    _e("backup_status", "GET", f"{P}/backup", None, m.BackupStatus, "Backups"),
    _e("backup_create", "POST", f"{P}/backup", m.BackupCreateRequest, m.BackupInfo, "Create a backup now"),
    _e("backup_restore", "POST", f"{P}/backup/restore", m.BackupRestoreRequest, m.BackupRestoreResult,
       "Restore a backup"),
)


@dataclass(frozen=True)
class StreamSpec:
    path: str
    protocol: str
    server_events: tuple[type[BaseModel], ...]
    client_frames: tuple[type[BaseModel], ...]


STREAM = StreamSpec(CHAT_STREAM_PATH, "websocket", EVENT_MODELS, CLIENT_FRAME_MODELS)


def event_type(model: type[BaseModel]) -> str:
    return str(model.model_fields["type"].default)


def all_models() -> dict[str, type[BaseModel]]:
    """Every model the contract exports, keyed by class name."""
    found: dict[str, type[BaseModel]] = {}
    for ep in ENDPOINTS:
        for model in (ep.request, ep.response):
            if model is not None:
                found[model.__name__] = model
    for model in (*STREAM.server_events, *STREAM.client_frames):
        found[model.__name__] = model
    return dict(sorted(found.items()))


def request_models() -> set[str]:
    return {ep.request.__name__ for ep in ENDPOINTS if ep.request is not None} | {
        x.__name__ for x in STREAM.client_frames
    }
