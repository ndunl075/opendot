"""Pydantic models for every HTTP endpoint the v0.1 UI calls (request and response).

Contract-first: the UI generates TypeScript from the JSON Schemas exported from these
models, and the mock server serves fake data built from them. Models only; the real
HTTP server is built in M2. Model class names must be unique across the contract
because each one is exported to its own schema file.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

API_VERSION = "v1"


class ApiModel(BaseModel):
    """Base for all contract models: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


# --- shared enums ----------------------------------------------------------------------------

RuleBehavior = Literal["auto", "auto_if_preapproved", "ask", "handoff"]
ReasoningEffort = Literal["low", "medium", "high"]
ModelTier = Literal["luna", "terra", "sol"]
TaskStatus = Literal["in_progress", "scheduled", "completed"]
ApprovalStatus = Literal["pending", "approved", "edited", "denied", "expired"]
ConnectionHealth = Literal["ok", "stale", "error", "never_synced"]
StylePreset = Literal["concise", "warm", "formal", "playful"]
ProviderId = Literal["chatgpt_plan", "openai_key", "anthropic_key", "openrouter", "local"]
ConnectionApp = Literal["gmail", "google_calendar", "github"]


class OkResponse(ApiModel):
    ok: bool = True
    message: str | None = None


class ErrorResponse(ApiModel):
    code: str
    message: str
    retryable: bool = False


# --- health and version ----------------------------------------------------------------------


class HealthResponse(ApiModel):
    status: Literal["ok", "degraded", "starting"]
    uptime_seconds: int
    database_ok: bool
    provider_ok: bool
    paused: bool
    checked_at: datetime


class VersionResponse(ApiModel):
    daemon_version: str
    api_version: str
    schema_version: int
    build: str | None = None


# --- onboarding and ChatGPT sign-in ----------------------------------------------------------

ChatGPTSignInState = Literal["signed_out", "pending", "signed_in", "error"]
PlanEligibility = Literal["unknown", "eligible_plus", "eligible_pro", "ineligible"]


class ChatGPTSignInStartRequest(ApiModel):
    open_browser: bool = True


class ChatGPTSignInStart(ApiModel):
    state: ChatGPTSignInState
    authorize_url: str
    poll_interval_seconds: int = 2
    expires_at: datetime


class ChatGPTStatus(ApiModel):
    state: ChatGPTSignInState
    plan: PlanEligibility
    plan_label: str | None = None
    """Shown as "Using ChatGPT plan" when signed in."""
    eligible: bool
    ineligible_reason: str | None = None
    account_label: str | None = None
    credits_enabled: bool = False
    """True only if the account allows paid credit use; OpenDot asks the user to keep it off."""
    manage_usage_url: str
    error: str | None = None


OnboardingStep = Literal["companion", "chatgpt", "weekly_limit", "connections", "intro", "done"]


class OnboardingState(ApiModel):
    current_step: OnboardingStep
    completed_steps: list[OnboardingStep]
    companion_name: str | None = None
    avatar_seed: str | None = None
    chatgpt: ChatGPTStatus
    weekly_limit_acknowledged: bool = False
    credits_off_acknowledged: bool = False
    intro_message: str | None = None


class CompanionSetupRequest(ApiModel):
    name: str = Field(min_length=1, max_length=40)
    avatar_seed: str = Field(min_length=1, max_length=64)


class OnboardingAcknowledgeRequest(ApiModel):
    weekly_limit_set: bool
    credits_off: bool


# --- companion profile -----------------------------------------------------------------------


class TaskActionResult(ApiModel):
    """The outcome of a user decision on a paused task (continue anyway, allow the top tier)."""

    task_id: str
    state: str
    """The task's state after the decision, as the agent loop reports it."""
    message: str = ""


class PlanLimitResumeResult(ApiModel):
    resumed_task_ids: list[str] = Field(default_factory=list)
    """Tasks that were paused by the plan usage limit and are running again."""


class CompanionProfile(ApiModel):
    name: str
    avatar_seed: str
    paused: bool
    paused_reason: str | None = None
    created_at: datetime
    style_preset: StylePreset


class CompanionTask(ApiModel):
    id: str
    title: str
    status: TaskStatus
    job_type: str
    created_at: datetime
    scheduled_for: datetime | None = None
    completed_at: datetime | None = None
    credits_used: float = 0.0
    summary: str | None = None


class CompanionTaskList(ApiModel):
    in_progress: list[CompanionTask]
    scheduled: list[CompanionTask]
    completed: list[CompanionTask]


class CompanionRenameRequest(ApiModel):
    name: str = Field(min_length=1, max_length=40)


class CompanionAvatarRequest(ApiModel):
    avatar_seed: str | None = None
    """Omit to let the daemon roll a new random seed."""


class CompanionPauseRequest(ApiModel):
    reason: str | None = None


class CompanionResetRequest(ApiModel):
    confirm: Literal["reset"]
    forget_memory: bool = False


# --- chat ------------------------------------------------------------------------------------


class ChatSendRequest(ApiModel):
    text: str = Field(min_length=1)
    conversation_id: str | None = None
    effort: ReasoningEffort | None = None
    tier: ModelTier | None = None
    """Optional per-message override; the router decides when omitted."""


class ChatSendAccepted(ApiModel):
    conversation_id: str
    message_id: str
    stream_path: str
    """WebSocket path that carries this message's ``StreamEvent`` frames."""


class UsageStamp(ApiModel):
    model: str
    effort: ReasoningEffort
    credits: float
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


class ToolCallRecord(ApiModel):
    call_id: str
    name: str
    summary: str
    status: Literal["running", "ok", "error", "needs_approval", "denied"]


class ChatMessage(ApiModel):
    id: str
    role: Literal["user", "assistant"]
    text: str
    created_at: datetime
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    approval_id: str | None = None
    usage: UsageStamp | None = None


class ConversationSummary(ApiModel):
    id: str
    title: str
    updated_at: datetime
    message_count: int


class ConversationList(ApiModel):
    conversations: list[ConversationSummary]


class Conversation(ApiModel):
    id: str
    title: str
    messages: list[ChatMessage]
    paused: bool = False


# --- approvals -------------------------------------------------------------------------------


class ApprovalItem(ApiModel):
    id: str
    status: ApprovalStatus
    action: str
    """Machine action name, for example ``gmail.create_draft``."""
    title: str
    preview: str
    payload: dict[str, str]
    """Editable fields shown in the approval card, as strings."""
    created_at: datetime
    expires_at: datetime | None = None
    rule_suggestion: str | None = None
    conversation_id: str | None = None
    decided_at: datetime | None = None
    review_note: str | None = None
    """The reviewer's note for this action (section 10: every ``ask`` action gets a reviewer pass)."""
    review_verdict: Literal["ok", "concern", "block"] | None = None


class ApprovalList(ApiModel):
    approvals: list[ApprovalItem]


class ApprovalApproveRequest(ApiModel):
    note: str | None = None


class ApprovalEditRequest(ApiModel):
    payload: dict[str, str]
    approve: bool = True


class ApprovalDenyRequest(ApiModel):
    reason: str | None = None


class ApprovalAlwaysAllowRequest(ApiModel):
    behavior: Literal["auto", "auto_if_preapproved"] = "auto_if_preapproved"
    scope_note: str | None = None


class ApprovalDecisionResult(ApiModel):
    approval: ApprovalItem
    executed: bool
    result_summary: str | None = None
    created_rule_id: str | None = None


# --- activity --------------------------------------------------------------------------------


class ActivityQuery(ApiModel):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None
    since: datetime | None = None


class ActivityItem(ApiModel):
    id: str
    at: datetime
    kind: Literal["action", "message", "approval", "reminder", "sync", "pause", "rule_change"]
    title: str
    why: str
    rule_id: str | None = None
    rule_name: str | None = None
    reviewer_note: str | None = None
    memory_ids: list[str] = Field(default_factory=list)
    approval_id: str | None = None
    credits: float = 0.0


class ActivityList(ApiModel):
    items: list[ActivityItem]
    next_cursor: str | None = None


# --- rules -----------------------------------------------------------------------------------


class Rule(ApiModel):
    id: str
    name: str
    action: str
    """Action pattern such as ``gmail.create_draft`` or ``calendar.*``."""
    behavior: RuleBehavior
    enabled: bool = True
    locked: bool = False
    """True for core deny-list items: shown in the UI, never editable."""
    core_deny: bool = False
    description: str | None = None
    created_at: datetime
    created_from_approval_id: str | None = None


class RuleList(ApiModel):
    rules: list[Rule]


class RuleCreateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=80)
    action: str = Field(min_length=1)
    behavior: RuleBehavior
    description: str | None = None


class RuleUpdateRequest(ApiModel):
    name: str | None = None
    behavior: RuleBehavior | None = None
    enabled: bool | None = None
    description: str | None = None


# --- memory ----------------------------------------------------------------------------------


class MemorySearchQuery(ApiModel):
    q: str = ""
    person: str | None = None
    source: str | None = None
    limit: int = Field(default=25, ge=1, le=100)


class MemoryItem(ApiModel):
    id: str
    statement: str
    source: str
    source_label: str
    people: list[str] = Field(default_factory=list)
    learned_at: datetime
    confidence: Literal["confirmed", "inferred"]
    superseded: bool = False


class MemorySearchResult(ApiModel):
    items: list[MemoryItem]
    total: int


class MemoryCorrectRequest(ApiModel):
    statement: str = Field(min_length=1)


class MemoryCorrectResult(ApiModel):
    corrected: MemoryItem
    superseded_id: str


class MemoryForgetRequest(ApiModel):
    scope: Literal["item", "source", "time", "person"]
    item_id: str | None = None
    source: str | None = None
    person: str | None = None
    since: datetime | None = None
    until: datetime | None = None


class MemoryForgetResult(ApiModel):
    forgotten_count: int
    scope: Literal["item", "source", "time", "person"]


# --- connections -----------------------------------------------------------------------------


class Connection(ApiModel):
    id: str
    app: ConnectionApp
    account_label: str
    health: ConnectionHealth
    last_synced_at: datetime | None = None
    health_detail: str | None = None
    read_only: bool = True
    write_opt_in: bool = False
    """Gmail drafts / Calendar events switch. Only meaningful for gmail and google_calendar."""
    write_opt_in_available: bool = False
    memory_item_count: int = 0


class ConnectionList(ApiModel):
    connections: list[Connection]


class ConnectionStartRequest(ApiModel):
    app: ConnectionApp


class ConnectionStart(ApiModel):
    app: ConnectionApp
    authorize_url: str
    state: str


class ConnectionWriteOptInRequest(ApiModel):
    enabled: bool


class ConnectionDisconnectRequest(ApiModel):
    forget_learned: bool = False
    """Also forget everything learned from this account."""


class ConnectionDisconnectResult(ApiModel):
    disconnected: bool
    forgotten_count: int = 0


# --- usage -----------------------------------------------------------------------------------


class UsageQuery(ApiModel):
    days: int = Field(default=7, ge=1, le=90)


class UsageByDay(ApiModel):
    day: str
    credits: float


class UsageByTask(ApiModel):
    task_id: str
    title: str
    credits: float


class UsageByJobType(ApiModel):
    job_type: str
    credits: float


class UsageBudgets(ApiModel):
    daily_credits: float | None = None
    task_credits: float | None = None
    daily_hard_stop: bool = True


class UsageSummary(ApiModel):
    days: int
    total_credits: float
    today_credits: float
    by_day: list[UsageByDay]
    by_task: list[UsageByTask]
    by_job_type: list[UsageByJobType]
    budgets: UsageBudgets
    daily_budget_remaining: float | None = None
    plan_label: str
    manage_usage_url: str
    paused_for_budget: bool = False


# --- settings --------------------------------------------------------------------------------


class KeepAwakeSettings(ApiModel):
    enabled: bool
    only_while_plugged_in: bool = True


class QuietHoursSettings(ApiModel):
    enabled: bool
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")
    timezone: str


class ProviderOptIn(ApiModel):
    provider: ProviderId
    label: str
    enabled: bool
    cost_warning: str | None = None
    """Shown next to the switch; required for every paid or third-party provider."""
    configured: bool = False
    feature_switches: list[str] = Field(default_factory=list)


class ModelTierOverride(ApiModel):
    job_type: str
    tier: ModelTier
    effort: ReasoningEffort | None = None


class Settings(ApiModel):
    keep_awake: KeepAwakeSettings
    quiet_hours: QuietHoursSettings
    providers: list[ProviderOptIn]
    tier_overrides: list[ModelTierOverride]
    auto_top_tier: bool = False
    style_preset: StylePreset


class SettingsUpdateRequest(ApiModel):
    keep_awake: KeepAwakeSettings | None = None
    quiet_hours: QuietHoursSettings | None = None
    provider_opt_ins: dict[ProviderId, bool] | None = None
    tier_overrides: list[ModelTierOverride] | None = None
    auto_top_tier: bool | None = None
    style_preset: StylePreset | None = None


# --- opt-in providers: feature switches, spend caps, API keys --------------------------------
# Saving an API key enables nothing; enabling a provider and turning on a feature are separate,
# explicit calls. No response model may carry a key or token: the key travels only in
# ProviderApiKeyRequest and the answer is ProviderKeyStatus (a boolean).

FeatureName = Literal["paid_fallback_when_plan_runs_out", "claude_as_reviewer", "smarter_memory_search"]
PaidProviderId = Literal["openai_key", "anthropic_key", "openrouter"]
OptInProviderId = Literal["openai_key", "anthropic_key", "openrouter", "local"]


class FeatureSwitchState(ApiModel):
    name: FeatureName
    title: str
    enabled: bool = False
    available: bool = False
    """True only when the switch is on AND a required provider is enabled."""
    cost_warning: str
    """Show this before the user turns the switch on."""
    requires_any_of: list[OptInProviderId]
    """At least one of these providers must be enabled for the switch to do anything."""
    requirement: str
    """Plain-language version of ``requires_any_of`` for the UI."""


class FeatureSwitchList(ApiModel):
    features: list[FeatureSwitchState]


class FeatureSwitchSetRequest(ApiModel):
    enabled: bool


class ProviderSpend(ApiModel):
    provider: PaidProviderId
    cap_usd: float = Field(ge=0)
    """Monthly cap in USD (UTC calendar month). 0 means no cap is set, so the provider refuses every call."""
    spent_usd: float = Field(ge=0)
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    cap_set: bool


class ProviderSpendList(ApiModel):
    providers: list[ProviderSpend]


class ProviderSpendCapRequest(ApiModel):
    cap_usd: float = Field(ge=0)


class ProviderApiKeyRequest(ApiModel):
    api_key: str = Field(min_length=1, repr=False)
    """Stored in the OS keychain. Never returned by any endpoint."""


class ProviderKeyStatus(ApiModel):
    provider: PaidProviderId
    key_saved: bool
    """Whether a key is stored. Saving a key does not enable the provider."""


class ProviderEnabledRequest(ApiModel):
    enabled: bool


class BackupInfo(ApiModel):
    id: str
    created_at: datetime
    size_bytes: int
    verified: bool


class BackupStatus(ApiModel):
    backups: list[BackupInfo]
    last_backup_at: datetime | None = None
    encryption_key_present: bool


class BackupCreateRequest(ApiModel):
    verify: bool = True


class BackupRestoreRequest(ApiModel):
    backup_id: str
    confirm: Literal["restore"]


class BackupRestoreResult(ApiModel):
    restored: bool
    backup_id: str
    restart_required: bool = True
