/* This file is generated from contract/. Do not edit. */

import type { ActivityList, ActivityQuery, ApprovalAlwaysAllowRequest, ApprovalApproveRequest, ApprovalDecisionResult, ApprovalDenyRequest, ApprovalEditRequest, ApprovalItem, ApprovalList, ApprovalRequiredEvent, BackupCreateRequest, BackupInfo, BackupRestoreRequest, BackupRestoreResult, BackupStatus, ChatGPTSignInStart, ChatGPTSignInStartRequest, ChatGPTStatus, ChatSendAccepted, ChatSendRequest, ClientPingFrame, ClientResumeFrame, ClientSendFrame, CompanionAvatarRequest, CompanionPauseRequest, CompanionProfile, CompanionRenameRequest, CompanionResetRequest, CompanionSetupRequest, CompanionTaskList, CompletedEvent, Connection, ConnectionDisconnectRequest, ConnectionDisconnectResult, ConnectionList, ConnectionStart, ConnectionStartRequest, ConnectionWriteOptInRequest, Conversation, ConversationList, ErrorEvent, FeatureSwitchList, FeatureSwitchSetRequest, FeatureSwitchState, HealthResponse, MemoryCorrectRequest, MemoryCorrectResult, MemoryForgetRequest, MemoryForgetResult, MemorySearchQuery, MemorySearchResult, MessageStartedEvent, OkResponse, OnboardingAcknowledgeRequest, OnboardingState, PausedEvent, PlanLimitResumeResult, ProviderApiKeyRequest, ProviderEnabledRequest, ProviderKeyStatus, ProviderOptIn, ProviderSpend, ProviderSpendCapRequest, ProviderSpendList, Rule, RuleCreateRequest, RuleList, RuleUpdateRequest, Settings, SettingsUpdateRequest, TaskActionResult, TextDeltaEvent, ToolCallEvent, UsageBudgets, UsageQuery, UsageSummary, VersionResponse } from "./types.gen";

export interface EndpointTable {
  "activity_list": { method: "GET"; path: "/v1/activity"; request: ActivityQuery; response: ActivityList; requestIn: "query" };
  "approval_always_allow": { method: "POST"; path: "/v1/approvals/{approval_id}/always-allow"; request: ApprovalAlwaysAllowRequest; response: ApprovalDecisionResult; requestIn: "body" };
  "approval_approve": { method: "POST"; path: "/v1/approvals/{approval_id}/approve"; request: ApprovalApproveRequest; response: ApprovalDecisionResult; requestIn: "body" };
  "approval_deny": { method: "POST"; path: "/v1/approvals/{approval_id}/deny"; request: ApprovalDenyRequest; response: ApprovalDecisionResult; requestIn: "body" };
  "approval_edit": { method: "POST"; path: "/v1/approvals/{approval_id}/edit"; request: ApprovalEditRequest; response: ApprovalDecisionResult; requestIn: "body" };
  "approval_get": { method: "GET"; path: "/v1/approvals/{approval_id}"; request: never; response: ApprovalItem; requestIn: null };
  "approvals_list": { method: "GET"; path: "/v1/approvals"; request: never; response: ApprovalList; requestIn: null };
  "backup_create": { method: "POST"; path: "/v1/backup"; request: BackupCreateRequest; response: BackupInfo; requestIn: "body" };
  "backup_restore": { method: "POST"; path: "/v1/backup/restore"; request: BackupRestoreRequest; response: BackupRestoreResult; requestIn: "body" };
  "backup_status": { method: "GET"; path: "/v1/backup"; request: never; response: BackupStatus; requestIn: null };
  "chat_conversation": { method: "GET"; path: "/v1/chat/conversations/{conversation_id}"; request: never; response: Conversation; requestIn: null };
  "chat_conversations": { method: "GET"; path: "/v1/chat/conversations"; request: never; response: ConversationList; requestIn: null };
  "chat_send": { method: "POST"; path: "/v1/chat/messages"; request: ChatSendRequest; response: ChatSendAccepted; requestIn: "body" };
  "chatgpt_disconnect": { method: "POST"; path: "/v1/auth/chatgpt/disconnect"; request: never; response: OkResponse; requestIn: null };
  "chatgpt_start": { method: "POST"; path: "/v1/auth/chatgpt/start"; request: ChatGPTSignInStartRequest; response: ChatGPTSignInStart; requestIn: "body" };
  "chatgpt_status": { method: "GET"; path: "/v1/auth/chatgpt/status"; request: never; response: ChatGPTStatus; requestIn: null };
  "companion_avatar": { method: "POST"; path: "/v1/companion/avatar"; request: CompanionAvatarRequest; response: CompanionProfile; requestIn: "body" };
  "companion_get": { method: "GET"; path: "/v1/companion"; request: never; response: CompanionProfile; requestIn: null };
  "companion_pause": { method: "POST"; path: "/v1/companion/pause"; request: CompanionPauseRequest; response: CompanionProfile; requestIn: "body" };
  "companion_rename": { method: "POST"; path: "/v1/companion/rename"; request: CompanionRenameRequest; response: CompanionProfile; requestIn: "body" };
  "companion_reset": { method: "POST"; path: "/v1/companion/reset"; request: CompanionResetRequest; response: CompanionProfile; requestIn: "body" };
  "companion_resume": { method: "POST"; path: "/v1/companion/resume"; request: never; response: CompanionProfile; requestIn: null };
  "companion_tasks": { method: "GET"; path: "/v1/companion/tasks"; request: never; response: CompanionTaskList; requestIn: null };
  "connection_disconnect": { method: "POST"; path: "/v1/connections/{connection_id}/disconnect"; request: ConnectionDisconnectRequest; response: ConnectionDisconnectResult; requestIn: "body" };
  "connection_start": { method: "POST"; path: "/v1/connections/start"; request: ConnectionStartRequest; response: ConnectionStart; requestIn: "body" };
  "connection_sync": { method: "POST"; path: "/v1/connections/{connection_id}/sync"; request: never; response: Connection; requestIn: null };
  "connection_write_opt_in": { method: "PUT"; path: "/v1/connections/{connection_id}/write-opt-in"; request: ConnectionWriteOptInRequest; response: Connection; requestIn: "body" };
  "connections_list": { method: "GET"; path: "/v1/connections"; request: never; response: ConnectionList; requestIn: null };
  "feature_set": { method: "PUT"; path: "/v1/providers/features/{feature}"; request: FeatureSwitchSetRequest; response: FeatureSwitchState; requestIn: "body" };
  "features_list": { method: "GET"; path: "/v1/providers/features"; request: never; response: FeatureSwitchList; requestIn: null };
  "health": { method: "GET"; path: "/v1/health"; request: never; response: HealthResponse; requestIn: null };
  "memory_correct": { method: "POST"; path: "/v1/memory/{memory_id}/correct"; request: MemoryCorrectRequest; response: MemoryCorrectResult; requestIn: "body" };
  "memory_forget": { method: "POST"; path: "/v1/memory/forget"; request: MemoryForgetRequest; response: MemoryForgetResult; requestIn: "body" };
  "memory_search": { method: "GET"; path: "/v1/memory"; request: MemorySearchQuery; response: MemorySearchResult; requestIn: "query" };
  "onboarding_acknowledge": { method: "POST"; path: "/v1/onboarding/acknowledge"; request: OnboardingAcknowledgeRequest; response: OnboardingState; requestIn: "body" };
  "onboarding_companion": { method: "POST"; path: "/v1/onboarding/companion"; request: CompanionSetupRequest; response: OnboardingState; requestIn: "body" };
  "onboarding_complete": { method: "POST"; path: "/v1/onboarding/complete"; request: never; response: OnboardingState; requestIn: null };
  "onboarding_get": { method: "GET"; path: "/v1/onboarding"; request: never; response: OnboardingState; requestIn: null };
  "plan_limit_resume": { method: "POST"; path: "/v1/companion/resume-plan-limit"; request: never; response: PlanLimitResumeResult; requestIn: null };
  "provider_api_key_remove": { method: "DELETE"; path: "/v1/providers/{provider}/api-key"; request: never; response: ProviderKeyStatus; requestIn: null };
  "provider_api_key_save": { method: "PUT"; path: "/v1/providers/{provider}/api-key"; request: ProviderApiKeyRequest; response: ProviderKeyStatus; requestIn: "body" };
  "provider_enabled_set": { method: "PUT"; path: "/v1/providers/{provider}/enabled"; request: ProviderEnabledRequest; response: ProviderOptIn; requestIn: "body" };
  "rule_create": { method: "POST"; path: "/v1/rules"; request: RuleCreateRequest; response: Rule; requestIn: "body" };
  "rule_delete": { method: "DELETE"; path: "/v1/rules/{rule_id}"; request: never; response: OkResponse; requestIn: null };
  "rule_update": { method: "PUT"; path: "/v1/rules/{rule_id}"; request: RuleUpdateRequest; response: Rule; requestIn: "body" };
  "rules_list": { method: "GET"; path: "/v1/rules"; request: never; response: RuleList; requestIn: null };
  "settings_get": { method: "GET"; path: "/v1/settings"; request: never; response: Settings; requestIn: null };
  "settings_update": { method: "PUT"; path: "/v1/settings"; request: SettingsUpdateRequest; response: Settings; requestIn: "body" };
  "spend_cap_set": { method: "PUT"; path: "/v1/providers/{provider}/spend-cap"; request: ProviderSpendCapRequest; response: ProviderSpend; requestIn: "body" };
  "spend_get": { method: "GET"; path: "/v1/providers/spend"; request: never; response: ProviderSpendList; requestIn: null };
  "task_approve_top_tier": { method: "POST"; path: "/v1/tasks/{task_id}/approve-top-tier"; request: never; response: TaskActionResult; requestIn: null };
  "task_continue": { method: "POST"; path: "/v1/tasks/{task_id}/continue"; request: never; response: TaskActionResult; requestIn: null };
  "usage_budgets_get": { method: "GET"; path: "/v1/usage/budgets"; request: never; response: UsageBudgets; requestIn: null };
  "usage_budgets_set": { method: "PUT"; path: "/v1/usage/budgets"; request: UsageBudgets; response: UsageBudgets; requestIn: "body" };
  "usage_get": { method: "GET"; path: "/v1/usage"; request: UsageQuery; response: UsageSummary; requestIn: "query" };
  "version": { method: "GET"; path: "/v1/version"; request: never; response: VersionResponse; requestIn: null };
}

export const endpointTable = {
  "activity_list": { method: "GET", path: "/v1/activity", requestIn: "query" },
  "approval_always_allow": { method: "POST", path: "/v1/approvals/{approval_id}/always-allow", requestIn: "body" },
  "approval_approve": { method: "POST", path: "/v1/approvals/{approval_id}/approve", requestIn: "body" },
  "approval_deny": { method: "POST", path: "/v1/approvals/{approval_id}/deny", requestIn: "body" },
  "approval_edit": { method: "POST", path: "/v1/approvals/{approval_id}/edit", requestIn: "body" },
  "approval_get": { method: "GET", path: "/v1/approvals/{approval_id}", requestIn: null },
  "approvals_list": { method: "GET", path: "/v1/approvals", requestIn: null },
  "backup_create": { method: "POST", path: "/v1/backup", requestIn: "body" },
  "backup_restore": { method: "POST", path: "/v1/backup/restore", requestIn: "body" },
  "backup_status": { method: "GET", path: "/v1/backup", requestIn: null },
  "chat_conversation": { method: "GET", path: "/v1/chat/conversations/{conversation_id}", requestIn: null },
  "chat_conversations": { method: "GET", path: "/v1/chat/conversations", requestIn: null },
  "chat_send": { method: "POST", path: "/v1/chat/messages", requestIn: "body" },
  "chatgpt_disconnect": { method: "POST", path: "/v1/auth/chatgpt/disconnect", requestIn: null },
  "chatgpt_start": { method: "POST", path: "/v1/auth/chatgpt/start", requestIn: "body" },
  "chatgpt_status": { method: "GET", path: "/v1/auth/chatgpt/status", requestIn: null },
  "companion_avatar": { method: "POST", path: "/v1/companion/avatar", requestIn: "body" },
  "companion_get": { method: "GET", path: "/v1/companion", requestIn: null },
  "companion_pause": { method: "POST", path: "/v1/companion/pause", requestIn: "body" },
  "companion_rename": { method: "POST", path: "/v1/companion/rename", requestIn: "body" },
  "companion_reset": { method: "POST", path: "/v1/companion/reset", requestIn: "body" },
  "companion_resume": { method: "POST", path: "/v1/companion/resume", requestIn: null },
  "companion_tasks": { method: "GET", path: "/v1/companion/tasks", requestIn: null },
  "connection_disconnect": { method: "POST", path: "/v1/connections/{connection_id}/disconnect", requestIn: "body" },
  "connection_start": { method: "POST", path: "/v1/connections/start", requestIn: "body" },
  "connection_sync": { method: "POST", path: "/v1/connections/{connection_id}/sync", requestIn: null },
  "connection_write_opt_in": { method: "PUT", path: "/v1/connections/{connection_id}/write-opt-in", requestIn: "body" },
  "connections_list": { method: "GET", path: "/v1/connections", requestIn: null },
  "feature_set": { method: "PUT", path: "/v1/providers/features/{feature}", requestIn: "body" },
  "features_list": { method: "GET", path: "/v1/providers/features", requestIn: null },
  "health": { method: "GET", path: "/v1/health", requestIn: null },
  "memory_correct": { method: "POST", path: "/v1/memory/{memory_id}/correct", requestIn: "body" },
  "memory_forget": { method: "POST", path: "/v1/memory/forget", requestIn: "body" },
  "memory_search": { method: "GET", path: "/v1/memory", requestIn: "query" },
  "onboarding_acknowledge": { method: "POST", path: "/v1/onboarding/acknowledge", requestIn: "body" },
  "onboarding_companion": { method: "POST", path: "/v1/onboarding/companion", requestIn: "body" },
  "onboarding_complete": { method: "POST", path: "/v1/onboarding/complete", requestIn: null },
  "onboarding_get": { method: "GET", path: "/v1/onboarding", requestIn: null },
  "plan_limit_resume": { method: "POST", path: "/v1/companion/resume-plan-limit", requestIn: null },
  "provider_api_key_remove": { method: "DELETE", path: "/v1/providers/{provider}/api-key", requestIn: null },
  "provider_api_key_save": { method: "PUT", path: "/v1/providers/{provider}/api-key", requestIn: "body" },
  "provider_enabled_set": { method: "PUT", path: "/v1/providers/{provider}/enabled", requestIn: "body" },
  "rule_create": { method: "POST", path: "/v1/rules", requestIn: "body" },
  "rule_delete": { method: "DELETE", path: "/v1/rules/{rule_id}", requestIn: null },
  "rule_update": { method: "PUT", path: "/v1/rules/{rule_id}", requestIn: "body" },
  "rules_list": { method: "GET", path: "/v1/rules", requestIn: null },
  "settings_get": { method: "GET", path: "/v1/settings", requestIn: null },
  "settings_update": { method: "PUT", path: "/v1/settings", requestIn: "body" },
  "spend_cap_set": { method: "PUT", path: "/v1/providers/{provider}/spend-cap", requestIn: "body" },
  "spend_get": { method: "GET", path: "/v1/providers/spend", requestIn: null },
  "task_approve_top_tier": { method: "POST", path: "/v1/tasks/{task_id}/approve-top-tier", requestIn: null },
  "task_continue": { method: "POST", path: "/v1/tasks/{task_id}/continue", requestIn: null },
  "usage_budgets_get": { method: "GET", path: "/v1/usage/budgets", requestIn: null },
  "usage_budgets_set": { method: "PUT", path: "/v1/usage/budgets", requestIn: "body" },
  "usage_get": { method: "GET", path: "/v1/usage", requestIn: "query" },
  "version": { method: "GET", path: "/v1/version", requestIn: null },
} as const;

export type EndpointName = keyof EndpointTable;
export type StreamEvent = ApprovalRequiredEvent | CompletedEvent | ErrorEvent | MessageStartedEvent | PausedEvent | TextDeltaEvent | ToolCallEvent;
export type ClientFrame = ClientPingFrame | ClientResumeFrame | ClientSendFrame;
export const streamPath = "/v1/chat/stream" as const;
