/* This file is generated from contract/. Do not edit. */

export namespace ActivityListSchema {
  export type ApprovalId = string | null;
  export type At = string;
  export type Credits = number;
  export type Id = string;
  export type Kind = 'action' | 'message' | 'approval' | 'reminder' | 'sync' | 'pause' | 'rule_change';
  export type MemoryIds = string[];
  export type ReviewerNote = string | null;
  export type RuleId = string | null;
  export type RuleName = string | null;
  export type Title = string;
  export type Why = string;
  export type Items = ActivityItem[];
  export type NextCursor = string | null;

  export interface ActivityList {
    items: Items;
    next_cursor?: NextCursor;
  }
  export interface ActivityItem {
    approval_id?: ApprovalId;
    at: At;
    credits?: Credits;
    id: Id;
    kind: Kind;
    memory_ids?: MemoryIds;
    reviewer_note?: ReviewerNote;
    rule_id?: RuleId;
    rule_name?: RuleName;
    title: Title;
    why: Why;
  }
}
export type ActivityList = ActivityListSchema.ActivityList;

export namespace ActivityQuerySchema {
  export type Cursor = string | null;
  export type Limit = number;
  export type Since = string | null;

  export interface ActivityQuery {
    cursor?: Cursor;
    limit?: Limit;
    since?: Since;
  }
}
export type ActivityQuery = ActivityQuerySchema.ActivityQuery;

export namespace ApprovalAlwaysAllowRequestSchema {
  export type Behavior = 'auto' | 'auto_if_preapproved';
  export type ScopeNote = string | null;

  export interface ApprovalAlwaysAllowRequest {
    behavior?: Behavior;
    scope_note?: ScopeNote;
  }
}
export type ApprovalAlwaysAllowRequest = ApprovalAlwaysAllowRequestSchema.ApprovalAlwaysAllowRequest;

export namespace ApprovalApproveRequestSchema {
  export type Note = string | null;

  export interface ApprovalApproveRequest {
    note?: Note;
  }
}
export type ApprovalApproveRequest = ApprovalApproveRequestSchema.ApprovalApproveRequest;

export namespace ApprovalDecisionResultSchema {
  export type Action = string;
  export type ConversationId = string | null;
  export type CreatedAt = string;
  export type DecidedAt = string | null;
  export type ExpiresAt = string | null;
  export type Id = string;
  export type Preview = string;
  export type ReviewNote = string | null;
  export type ReviewVerdict = ('ok' | 'concern' | 'block') | null;
  export type RuleSuggestion = string | null;
  export type Status = 'pending' | 'approved' | 'edited' | 'denied' | 'expired';
  export type Title = string;
  export type CreatedRuleId = string | null;
  export type Executed = boolean;
  export type ResultSummary = string | null;

  export interface ApprovalDecisionResult {
    approval: ApprovalItem;
    created_rule_id?: CreatedRuleId;
    executed: Executed;
    result_summary?: ResultSummary;
  }
  export interface ApprovalItem {
    action: Action;
    conversation_id?: ConversationId;
    created_at: CreatedAt;
    decided_at?: DecidedAt;
    expires_at?: ExpiresAt;
    id: Id;
    payload: Payload;
    preview: Preview;
    review_note?: ReviewNote;
    review_verdict?: ReviewVerdict;
    rule_suggestion?: RuleSuggestion;
    status: Status;
    title: Title;
  }
  export interface Payload {
    [k: string]: string;
  }
}
export type ApprovalDecisionResult = ApprovalDecisionResultSchema.ApprovalDecisionResult;

export namespace ApprovalDenyRequestSchema {
  export type Reason = string | null;

  export interface ApprovalDenyRequest {
    reason?: Reason;
  }
}
export type ApprovalDenyRequest = ApprovalDenyRequestSchema.ApprovalDenyRequest;

export namespace ApprovalEditRequestSchema {
  export type Approve = boolean;

  export interface ApprovalEditRequest {
    approve?: Approve;
    payload: Payload;
  }
  export interface Payload {
    [k: string]: string;
  }
}
export type ApprovalEditRequest = ApprovalEditRequestSchema.ApprovalEditRequest;

export namespace ApprovalItemSchema {
  export type Action = string;
  export type ConversationId = string | null;
  export type CreatedAt = string;
  export type DecidedAt = string | null;
  export type ExpiresAt = string | null;
  export type Id = string;
  export type Preview = string;
  export type ReviewNote = string | null;
  export type ReviewVerdict = ('ok' | 'concern' | 'block') | null;
  export type RuleSuggestion = string | null;
  export type Status = 'pending' | 'approved' | 'edited' | 'denied' | 'expired';
  export type Title = string;

  export interface ApprovalItem {
    action: Action;
    conversation_id?: ConversationId;
    created_at: CreatedAt;
    decided_at?: DecidedAt;
    expires_at?: ExpiresAt;
    id: Id;
    payload: Payload;
    preview: Preview;
    review_note?: ReviewNote;
    review_verdict?: ReviewVerdict;
    rule_suggestion?: RuleSuggestion;
    status: Status;
    title: Title;
  }
  export interface Payload {
    [k: string]: string;
  }
}
export type ApprovalItem = ApprovalItemSchema.ApprovalItem;

export namespace ApprovalListSchema {
  export type Action = string;
  export type ConversationId = string | null;
  export type CreatedAt = string;
  export type DecidedAt = string | null;
  export type ExpiresAt = string | null;
  export type Id = string;
  export type Preview = string;
  export type ReviewNote = string | null;
  export type ReviewVerdict = ('ok' | 'concern' | 'block') | null;
  export type RuleSuggestion = string | null;
  export type Status = 'pending' | 'approved' | 'edited' | 'denied' | 'expired';
  export type Title = string;
  export type Approvals = ApprovalItem[];

  export interface ApprovalList {
    approvals: Approvals;
  }
  export interface ApprovalItem {
    action: Action;
    conversation_id?: ConversationId;
    created_at: CreatedAt;
    decided_at?: DecidedAt;
    expires_at?: ExpiresAt;
    id: Id;
    payload: Payload;
    preview: Preview;
    review_note?: ReviewNote;
    review_verdict?: ReviewVerdict;
    rule_suggestion?: RuleSuggestion;
    status: Status;
    title: Title;
  }
  export interface Payload {
    [k: string]: string;
  }
}
export type ApprovalList = ApprovalListSchema.ApprovalList;

export namespace ApprovalRequiredEventSchema {
  export type Action = string;
  export type ConversationId = string | null;
  export type CreatedAt = string;
  export type DecidedAt = string | null;
  export type ExpiresAt = string | null;
  export type Id = string;
  export type Preview = string;
  export type ReviewNote = string | null;
  export type ReviewVerdict = ('ok' | 'concern' | 'block') | null;
  export type RuleSuggestion = string | null;
  export type Status = 'pending' | 'approved' | 'edited' | 'denied' | 'expired';
  export type Title = string;
  export type ConversationId1 = string;
  export type MessageId = string;
  export type Seq = number;
  export type Type = 'approval_required';

  export interface ApprovalRequiredEvent {
    approval: ApprovalItem;
    conversation_id: ConversationId1;
    message_id: MessageId;
    seq: Seq;
    type?: Type;
  }
  export interface ApprovalItem {
    action: Action;
    conversation_id?: ConversationId;
    created_at: CreatedAt;
    decided_at?: DecidedAt;
    expires_at?: ExpiresAt;
    id: Id;
    payload: Payload;
    preview: Preview;
    review_note?: ReviewNote;
    review_verdict?: ReviewVerdict;
    rule_suggestion?: RuleSuggestion;
    status: Status;
    title: Title;
  }
  export interface Payload {
    [k: string]: string;
  }
}
export type ApprovalRequiredEvent = ApprovalRequiredEventSchema.ApprovalRequiredEvent;

export namespace BackupCreateRequestSchema {
  export type Verify = boolean;

  export interface BackupCreateRequest {
    verify?: Verify;
  }
}
export type BackupCreateRequest = BackupCreateRequestSchema.BackupCreateRequest;

export namespace BackupInfoSchema {
  export type CreatedAt = string;
  export type Id = string;
  export type SizeBytes = number;
  export type Verified = boolean;

  export interface BackupInfo {
    created_at: CreatedAt;
    id: Id;
    size_bytes: SizeBytes;
    verified: Verified;
  }
}
export type BackupInfo = BackupInfoSchema.BackupInfo;

export namespace BackupRestoreRequestSchema {
  export type BackupId = string;
  export type Confirm = 'restore';

  export interface BackupRestoreRequest {
    backup_id: BackupId;
    confirm: Confirm;
  }
}
export type BackupRestoreRequest = BackupRestoreRequestSchema.BackupRestoreRequest;

export namespace BackupRestoreResultSchema {
  export type BackupId = string;
  export type RestartRequired = boolean;
  export type Restored = boolean;

  export interface BackupRestoreResult {
    backup_id: BackupId;
    restart_required?: RestartRequired;
    restored: Restored;
  }
}
export type BackupRestoreResult = BackupRestoreResultSchema.BackupRestoreResult;

export namespace BackupStatusSchema {
  export type CreatedAt = string;
  export type Id = string;
  export type SizeBytes = number;
  export type Verified = boolean;
  export type Backups = BackupInfo[];
  export type EncryptionKeyPresent = boolean;
  export type LastBackupAt = string | null;

  export interface BackupStatus {
    backups: Backups;
    encryption_key_present: EncryptionKeyPresent;
    last_backup_at?: LastBackupAt;
  }
  export interface BackupInfo {
    created_at: CreatedAt;
    id: Id;
    size_bytes: SizeBytes;
    verified: Verified;
  }
}
export type BackupStatus = BackupStatusSchema.BackupStatus;

export namespace ChatGPTSignInStartSchema {
  export type AuthorizeUrl = string;
  export type ExpiresAt = string;
  export type PollIntervalSeconds = number;
  export type State = 'signed_out' | 'pending' | 'signed_in' | 'error';

  export interface ChatGPTSignInStart {
    authorize_url: AuthorizeUrl;
    expires_at: ExpiresAt;
    poll_interval_seconds?: PollIntervalSeconds;
    state: State;
  }
}
export type ChatGPTSignInStart = ChatGPTSignInStartSchema.ChatGPTSignInStart;

export namespace ChatGPTSignInStartRequestSchema {
  export type OpenBrowser = boolean;

  export interface ChatGPTSignInStartRequest {
    open_browser?: OpenBrowser;
  }
}
export type ChatGPTSignInStartRequest = ChatGPTSignInStartRequestSchema.ChatGPTSignInStartRequest;

export namespace ChatGPTStatusSchema {
  export type AccountLabel = string | null;
  export type CreditsEnabled = boolean;
  export type Eligible = boolean;
  export type Error = string | null;
  export type IneligibleReason = string | null;
  export type ManageUsageUrl = string;
  export type Plan = 'unknown' | 'eligible_plus' | 'eligible_pro' | 'ineligible';
  export type PlanLabel = string | null;
  export type State = 'signed_out' | 'pending' | 'signed_in' | 'error';

  export interface ChatGPTStatus {
    account_label?: AccountLabel;
    credits_enabled?: CreditsEnabled;
    eligible: Eligible;
    error?: Error;
    ineligible_reason?: IneligibleReason;
    manage_usage_url: ManageUsageUrl;
    plan: Plan;
    plan_label?: PlanLabel;
    state: State;
  }
}
export type ChatGPTStatus = ChatGPTStatusSchema.ChatGPTStatus;

export namespace ChatSendAcceptedSchema {
  export type ConversationId = string;
  export type MessageId = string;
  export type StreamPath = string;

  export interface ChatSendAccepted {
    conversation_id: ConversationId;
    message_id: MessageId;
    stream_path: StreamPath;
  }
}
export type ChatSendAccepted = ChatSendAcceptedSchema.ChatSendAccepted;

export namespace ChatSendRequestSchema {
  export type ConversationId = string | null;
  export type Effort = ('low' | 'medium' | 'high') | null;
  export type Text = string;
  export type Tier = ('luna' | 'terra' | 'sol') | null;

  export interface ChatSendRequest {
    conversation_id?: ConversationId;
    effort?: Effort;
    text: Text;
    tier?: Tier;
  }
}
export type ChatSendRequest = ChatSendRequestSchema.ChatSendRequest;

export namespace ClientFrameSchema {
  export type ClientFrame = ClientSendFrame | ClientResumeFrame | ClientPingFrame;
  export type ConversationId = string | null;
  export type Text = string;
  export type Type = 'send';
  export type AfterSeq = number;
  export type ConversationId1 = string;
  export type Type1 = 'resume';
  export type Type2 = 'ping';

  export interface ClientSendFrame {
    conversation_id?: ConversationId;
    text: Text;
    type?: Type;
  }
  export interface ClientResumeFrame {
    after_seq: AfterSeq;
    conversation_id: ConversationId1;
    type?: Type1;
  }
  export interface ClientPingFrame {
    type?: Type2;
  }
}
export type ClientFrame = ClientFrameSchema.ClientFrame;

export namespace ClientPingFrameSchema {
  export type Type = 'ping';

  export interface ClientPingFrame {
    type?: Type;
  }
}
export type ClientPingFrame = ClientPingFrameSchema.ClientPingFrame;

export namespace ClientResumeFrameSchema {
  export type AfterSeq = number;
  export type ConversationId = string;
  export type Type = 'resume';

  export interface ClientResumeFrame {
    after_seq: AfterSeq;
    conversation_id: ConversationId;
    type?: Type;
  }
}
export type ClientResumeFrame = ClientResumeFrameSchema.ClientResumeFrame;

export namespace ClientSendFrameSchema {
  export type ConversationId = string | null;
  export type Text = string;
  export type Type = 'send';

  export interface ClientSendFrame {
    conversation_id?: ConversationId;
    text: Text;
    type?: Type;
  }
}
export type ClientSendFrame = ClientSendFrameSchema.ClientSendFrame;

export namespace CompanionAvatarRequestSchema {
  export type AvatarSeed = string | null;

  export interface CompanionAvatarRequest {
    avatar_seed?: AvatarSeed;
  }
}
export type CompanionAvatarRequest = CompanionAvatarRequestSchema.CompanionAvatarRequest;

export namespace CompanionPauseRequestSchema {
  export type Reason = string | null;

  export interface CompanionPauseRequest {
    reason?: Reason;
  }
}
export type CompanionPauseRequest = CompanionPauseRequestSchema.CompanionPauseRequest;

export namespace CompanionProfileSchema {
  export type AvatarSeed = string;
  export type CreatedAt = string;
  export type Name = string;
  export type Paused = boolean;
  export type PausedReason = string | null;
  export type StylePreset = 'concise' | 'warm' | 'formal' | 'playful';

  export interface CompanionProfile {
    avatar_seed: AvatarSeed;
    created_at: CreatedAt;
    name: Name;
    paused: Paused;
    paused_reason?: PausedReason;
    style_preset: StylePreset;
  }
}
export type CompanionProfile = CompanionProfileSchema.CompanionProfile;

export namespace CompanionRenameRequestSchema {
  export type Name = string;

  export interface CompanionRenameRequest {
    name: Name;
  }
}
export type CompanionRenameRequest = CompanionRenameRequestSchema.CompanionRenameRequest;

export namespace CompanionResetRequestSchema {
  export type Confirm = 'reset';
  export type ForgetMemory = boolean;

  export interface CompanionResetRequest {
    confirm: Confirm;
    forget_memory?: ForgetMemory;
  }
}
export type CompanionResetRequest = CompanionResetRequestSchema.CompanionResetRequest;

export namespace CompanionSetupRequestSchema {
  export type AvatarSeed = string;
  export type Name = string;

  export interface CompanionSetupRequest {
    avatar_seed: AvatarSeed;
    name: Name;
  }
}
export type CompanionSetupRequest = CompanionSetupRequestSchema.CompanionSetupRequest;

export namespace CompanionTaskListSchema {
  export type CompletedAt = string | null;
  export type CreatedAt = string;
  export type CreditsUsed = number;
  export type Id = string;
  export type JobType = string;
  export type ScheduledFor = string | null;
  export type Status = 'in_progress' | 'scheduled' | 'completed';
  export type Summary = string | null;
  export type Title = string;
  export type Completed = CompanionTask[];
  export type InProgress = CompanionTask[];
  export type Scheduled = CompanionTask[];

  export interface CompanionTaskList {
    completed: Completed;
    in_progress: InProgress;
    scheduled: Scheduled;
  }
  export interface CompanionTask {
    completed_at?: CompletedAt;
    created_at: CreatedAt;
    credits_used?: CreditsUsed;
    id: Id;
    job_type: JobType;
    scheduled_for?: ScheduledFor;
    status: Status;
    summary?: Summary;
    title: Title;
  }
}
export type CompanionTaskList = CompanionTaskListSchema.CompanionTaskList;

export namespace CompletedEventSchema {
  export type ConversationId = string;
  export type MessageId = string;
  export type Seq = number;
  export type Text = string;
  export type Type = 'completed';
  export type CachedInputTokens = number;
  export type Credits = number;
  export type Effort = 'low' | 'medium' | 'high';
  export type InputTokens = number;
  export type Model = string;
  export type OutputTokens = number;

  export interface CompletedEvent {
    conversation_id: ConversationId;
    message_id: MessageId;
    seq: Seq;
    text: Text;
    type?: Type;
    usage: UsageStamp;
  }
  export interface UsageStamp {
    cached_input_tokens?: CachedInputTokens;
    credits: Credits;
    effort: Effort;
    input_tokens?: InputTokens;
    model: Model;
    output_tokens?: OutputTokens;
  }
}
export type CompletedEvent = CompletedEventSchema.CompletedEvent;

export namespace ConnectionSchema {
  export type AccountLabel = string;
  export type App = 'gmail' | 'google_calendar' | 'github';
  export type AuthorizeUrl = string | null;
  export type Health = 'ok' | 'stale' | 'error' | 'never_synced';
  export type HealthDetail = string | null;
  export type Id = string;
  export type LastSyncedAt = string | null;
  export type MemoryItemCount = number;
  export type ReadOnly = boolean;
  export type WriteOptIn = boolean;
  export type WriteOptInAvailable = boolean;

  export interface Connection {
    account_label: AccountLabel;
    app: App;
    authorize_url?: AuthorizeUrl;
    health: Health;
    health_detail?: HealthDetail;
    id: Id;
    last_synced_at?: LastSyncedAt;
    memory_item_count?: MemoryItemCount;
    read_only?: ReadOnly;
    write_opt_in?: WriteOptIn;
    write_opt_in_available?: WriteOptInAvailable;
  }
}
export type Connection = ConnectionSchema.Connection;

export namespace ConnectionDisconnectRequestSchema {
  export type ForgetLearned = boolean;

  export interface ConnectionDisconnectRequest {
    forget_learned?: ForgetLearned;
  }
}
export type ConnectionDisconnectRequest = ConnectionDisconnectRequestSchema.ConnectionDisconnectRequest;

export namespace ConnectionDisconnectResultSchema {
  export type Disconnected = boolean;
  export type ForgottenCount = number;

  export interface ConnectionDisconnectResult {
    disconnected: Disconnected;
    forgotten_count?: ForgottenCount;
  }
}
export type ConnectionDisconnectResult = ConnectionDisconnectResultSchema.ConnectionDisconnectResult;

export namespace ConnectionListSchema {
  export type AccountLabel = string;
  export type App = 'gmail' | 'google_calendar' | 'github';
  export type AuthorizeUrl = string | null;
  export type Health = 'ok' | 'stale' | 'error' | 'never_synced';
  export type HealthDetail = string | null;
  export type Id = string;
  export type LastSyncedAt = string | null;
  export type MemoryItemCount = number;
  export type ReadOnly = boolean;
  export type WriteOptIn = boolean;
  export type WriteOptInAvailable = boolean;
  export type Connections = Connection[];

  export interface ConnectionList {
    connections: Connections;
  }
  export interface Connection {
    account_label: AccountLabel;
    app: App;
    authorize_url?: AuthorizeUrl;
    health: Health;
    health_detail?: HealthDetail;
    id: Id;
    last_synced_at?: LastSyncedAt;
    memory_item_count?: MemoryItemCount;
    read_only?: ReadOnly;
    write_opt_in?: WriteOptIn;
    write_opt_in_available?: WriteOptInAvailable;
  }
}
export type ConnectionList = ConnectionListSchema.ConnectionList;

export namespace ConnectionStartSchema {
  export type App = 'gmail' | 'google_calendar' | 'github';
  export type AuthorizeUrl = string;
  export type State = string;

  export interface ConnectionStart {
    app: App;
    authorize_url: AuthorizeUrl;
    state: State;
  }
}
export type ConnectionStart = ConnectionStartSchema.ConnectionStart;

export namespace ConnectionStartRequestSchema {
  export type App = 'gmail' | 'google_calendar' | 'github';

  export interface ConnectionStartRequest {
    app: App;
  }
}
export type ConnectionStartRequest = ConnectionStartRequestSchema.ConnectionStartRequest;

export namespace ConnectionWriteOptInRequestSchema {
  export type Enabled = boolean;

  export interface ConnectionWriteOptInRequest {
    enabled: Enabled;
  }
}
export type ConnectionWriteOptInRequest = ConnectionWriteOptInRequestSchema.ConnectionWriteOptInRequest;

export namespace ConversationSchema {
  export type Id = string;
  export type ApprovalId = string | null;
  export type CreatedAt = string;
  export type Id1 = string;
  export type Role = 'user' | 'assistant';
  export type Text = string;
  export type CallId = string;
  export type Name = string;
  export type Status = 'running' | 'ok' | 'error' | 'needs_approval' | 'denied';
  export type Summary = string;
  export type ToolCalls = ToolCallRecord[];
  export type CachedInputTokens = number;
  export type Credits = number;
  export type Effort = 'low' | 'medium' | 'high';
  export type InputTokens = number;
  export type Model = string;
  export type OutputTokens = number;
  export type Messages = ChatMessage[];
  export type Paused = boolean;
  export type Title = string;

  export interface Conversation {
    id: Id;
    messages: Messages;
    paused?: Paused;
    title: Title;
  }
  export interface ChatMessage {
    approval_id?: ApprovalId;
    created_at: CreatedAt;
    id: Id1;
    role: Role;
    text: Text;
    tool_calls?: ToolCalls;
    usage?: UsageStamp | null;
  }
  export interface ToolCallRecord {
    call_id: CallId;
    name: Name;
    status: Status;
    summary: Summary;
  }
  export interface UsageStamp {
    cached_input_tokens?: CachedInputTokens;
    credits: Credits;
    effort: Effort;
    input_tokens?: InputTokens;
    model: Model;
    output_tokens?: OutputTokens;
  }
}
export type Conversation = ConversationSchema.Conversation;

export namespace ConversationListSchema {
  export type Id = string;
  export type MessageCount = number;
  export type Title = string;
  export type UpdatedAt = string;
  export type Conversations = ConversationSummary[];

  export interface ConversationList {
    conversations: Conversations;
  }
  export interface ConversationSummary {
    id: Id;
    message_count: MessageCount;
    title: Title;
    updated_at: UpdatedAt;
  }
}
export type ConversationList = ConversationListSchema.ConversationList;

export namespace ErrorEventSchema {
  export type Code = string;
  export type ConversationId = string;
  export type Message = string;
  export type MessageId = string;
  export type Retryable = boolean;
  export type Seq = number;
  export type Type = 'error';

  export interface ErrorEvent {
    code: Code;
    conversation_id: ConversationId;
    message: Message;
    message_id: MessageId;
    retryable?: Retryable;
    seq: Seq;
    type?: Type;
  }
}
export type ErrorEvent = ErrorEventSchema.ErrorEvent;

export namespace FeatureSwitchListSchema {
  export type Available = boolean;
  export type CostWarning = string;
  export type Enabled = boolean;
  export type Name = 'paid_fallback_when_plan_runs_out' | 'claude_as_reviewer' | 'smarter_memory_search';
  export type Requirement = string;
  export type RequiresAnyOf = ('openai_key' | 'anthropic_key' | 'openrouter' | 'local')[];
  export type Title = string;
  export type Features = FeatureSwitchState[];

  export interface FeatureSwitchList {
    features: Features;
  }
  export interface FeatureSwitchState {
    available?: Available;
    cost_warning: CostWarning;
    enabled?: Enabled;
    name: Name;
    requirement: Requirement;
    requires_any_of: RequiresAnyOf;
    title: Title;
  }
}
export type FeatureSwitchList = FeatureSwitchListSchema.FeatureSwitchList;

export namespace FeatureSwitchSetRequestSchema {
  export type Enabled = boolean;

  export interface FeatureSwitchSetRequest {
    enabled: Enabled;
  }
}
export type FeatureSwitchSetRequest = FeatureSwitchSetRequestSchema.FeatureSwitchSetRequest;

export namespace FeatureSwitchStateSchema {
  export type Available = boolean;
  export type CostWarning = string;
  export type Enabled = boolean;
  export type Name = 'paid_fallback_when_plan_runs_out' | 'claude_as_reviewer' | 'smarter_memory_search';
  export type Requirement = string;
  export type RequiresAnyOf = ('openai_key' | 'anthropic_key' | 'openrouter' | 'local')[];
  export type Title = string;

  export interface FeatureSwitchState {
    available?: Available;
    cost_warning: CostWarning;
    enabled?: Enabled;
    name: Name;
    requirement: Requirement;
    requires_any_of: RequiresAnyOf;
    title: Title;
  }
}
export type FeatureSwitchState = FeatureSwitchStateSchema.FeatureSwitchState;

export namespace GithubTokenRequestSchema {
  export type Token = string;

  export interface GithubTokenRequest {
    token: Token;
  }
}
export type GithubTokenRequest = GithubTokenRequestSchema.GithubTokenRequest;

export namespace GithubTokenStatusSchema {
  export type Configured = boolean;

  export interface GithubTokenStatus {
    configured: Configured;
  }
}
export type GithubTokenStatus = GithubTokenStatusSchema.GithubTokenStatus;

export namespace GoogleClientRequestSchema {
  export type ClientId = string;
  export type ClientSecret = string;

  /**
   * The user's own Google OAuth client ("Desktop app" type). Stored only in the OS keychain.
   */
  export interface GoogleClientRequest {
    client_id: ClientId;
    client_secret: ClientSecret;
  }
}
export type GoogleClientRequest = GoogleClientRequestSchema.GoogleClientRequest;

export namespace GoogleClientStatusSchema {
  export type Configured = boolean;
  export type SetupSteps = string[];

  export interface GoogleClientStatus {
    configured: Configured;
    setup_steps?: SetupSteps;
  }
}
export type GoogleClientStatus = GoogleClientStatusSchema.GoogleClientStatus;

export namespace HealthResponseSchema {
  export type CheckedAt = string;
  export type DatabaseOk = boolean;
  export type Paused = boolean;
  export type ProviderOk = boolean;
  export type Status = 'ok' | 'degraded' | 'starting';
  export type UptimeSeconds = number;

  export interface HealthResponse {
    checked_at: CheckedAt;
    database_ok: DatabaseOk;
    paused: Paused;
    provider_ok: ProviderOk;
    status: Status;
    uptime_seconds: UptimeSeconds;
  }
}
export type HealthResponse = HealthResponseSchema.HealthResponse;

export namespace MemoryCorrectRequestSchema {
  export type Statement = string;

  export interface MemoryCorrectRequest {
    statement: Statement;
  }
}
export type MemoryCorrectRequest = MemoryCorrectRequestSchema.MemoryCorrectRequest;

export namespace MemoryCorrectResultSchema {
  export type Confidence = 'confirmed' | 'inferred';
  export type Id = string;
  export type LearnedAt = string;
  export type People = string[];
  export type Source = string;
  export type SourceLabel = string;
  export type Statement = string;
  export type Superseded = boolean;
  export type SupersededId = string;

  export interface MemoryCorrectResult {
    corrected: MemoryItem;
    superseded_id: SupersededId;
  }
  export interface MemoryItem {
    confidence: Confidence;
    id: Id;
    learned_at: LearnedAt;
    people?: People;
    source: Source;
    source_label: SourceLabel;
    statement: Statement;
    superseded?: Superseded;
  }
}
export type MemoryCorrectResult = MemoryCorrectResultSchema.MemoryCorrectResult;

export namespace MemoryForgetRequestSchema {
  export type ItemId = string | null;
  export type Person = string | null;
  export type Scope = 'item' | 'source' | 'time' | 'person';
  export type Since = string | null;
  export type Source = string | null;
  export type Until = string | null;

  export interface MemoryForgetRequest {
    item_id?: ItemId;
    person?: Person;
    scope: Scope;
    since?: Since;
    source?: Source;
    until?: Until;
  }
}
export type MemoryForgetRequest = MemoryForgetRequestSchema.MemoryForgetRequest;

export namespace MemoryForgetResultSchema {
  export type ForgottenCount = number;
  export type Scope = 'item' | 'source' | 'time' | 'person';

  export interface MemoryForgetResult {
    forgotten_count: ForgottenCount;
    scope: Scope;
  }
}
export type MemoryForgetResult = MemoryForgetResultSchema.MemoryForgetResult;

export namespace MemorySearchQuerySchema {
  export type Limit = number;
  export type Person = string | null;
  export type Q = string;
  export type Source = string | null;

  export interface MemorySearchQuery {
    limit?: Limit;
    person?: Person;
    q?: Q;
    source?: Source;
  }
}
export type MemorySearchQuery = MemorySearchQuerySchema.MemorySearchQuery;

export namespace MemorySearchResultSchema {
  export type Confidence = 'confirmed' | 'inferred';
  export type Id = string;
  export type LearnedAt = string;
  export type People = string[];
  export type Source = string;
  export type SourceLabel = string;
  export type Statement = string;
  export type Superseded = boolean;
  export type Items = MemoryItem[];
  export type Total = number;

  export interface MemorySearchResult {
    items: Items;
    total: Total;
  }
  export interface MemoryItem {
    confidence: Confidence;
    id: Id;
    learned_at: LearnedAt;
    people?: People;
    source: Source;
    source_label: SourceLabel;
    statement: Statement;
    superseded?: Superseded;
  }
}
export type MemorySearchResult = MemorySearchResultSchema.MemorySearchResult;

export namespace MessageStartedEventSchema {
  export type ConversationId = string;
  export type MessageId = string;
  export type Seq = number;
  export type Type = 'message_started';

  export interface MessageStartedEvent {
    conversation_id: ConversationId;
    message_id: MessageId;
    seq: Seq;
    type?: Type;
  }
}
export type MessageStartedEvent = MessageStartedEventSchema.MessageStartedEvent;

export namespace OkResponseSchema {
  export type Message = string | null;
  export type Ok = boolean;

  export interface OkResponse {
    message?: Message;
    ok?: Ok;
  }
}
export type OkResponse = OkResponseSchema.OkResponse;

export namespace OnboardingAcknowledgeRequestSchema {
  export type CreditsOff = boolean;
  export type WeeklyLimitSet = boolean;

  export interface OnboardingAcknowledgeRequest {
    credits_off: CreditsOff;
    weekly_limit_set: WeeklyLimitSet;
  }
}
export type OnboardingAcknowledgeRequest = OnboardingAcknowledgeRequestSchema.OnboardingAcknowledgeRequest;

export namespace OnboardingStateSchema {
  export type AvatarSeed = string | null;
  export type AccountLabel = string | null;
  export type CreditsEnabled = boolean;
  export type Eligible = boolean;
  export type Error = string | null;
  export type IneligibleReason = string | null;
  export type ManageUsageUrl = string;
  export type Plan = 'unknown' | 'eligible_plus' | 'eligible_pro' | 'ineligible';
  export type PlanLabel = string | null;
  export type State = 'signed_out' | 'pending' | 'signed_in' | 'error';
  export type CompanionName = string | null;
  export type CompletedSteps = ('companion' | 'chatgpt' | 'weekly_limit' | 'connections' | 'intro' | 'done')[];
  export type CreditsOffAcknowledged = boolean;
  export type CurrentStep = 'companion' | 'chatgpt' | 'weekly_limit' | 'connections' | 'intro' | 'done';
  export type IntroMessage = string | null;
  export type WeeklyLimitAcknowledged = boolean;

  export interface OnboardingState {
    avatar_seed?: AvatarSeed;
    chatgpt: ChatGPTStatus;
    companion_name?: CompanionName;
    completed_steps: CompletedSteps;
    credits_off_acknowledged?: CreditsOffAcknowledged;
    current_step: CurrentStep;
    intro_message?: IntroMessage;
    weekly_limit_acknowledged?: WeeklyLimitAcknowledged;
  }
  export interface ChatGPTStatus {
    account_label?: AccountLabel;
    credits_enabled?: CreditsEnabled;
    eligible: Eligible;
    error?: Error;
    ineligible_reason?: IneligibleReason;
    manage_usage_url: ManageUsageUrl;
    plan: Plan;
    plan_label?: PlanLabel;
    state: State;
  }
}
export type OnboardingState = OnboardingStateSchema.OnboardingState;

export namespace PausedEventSchema {
  export type ConversationId = string;
  export type Message = string;
  export type MessageId = string;
  export type Reason = 'user' | 'task_budget' | 'daily_budget' | 'rate_limited' | 'anomaly' | 'top_tier_approval';
  export type ResumeAt = string | null;
  export type Seq = number;
  export type TaskId = string | null;
  export type Type = 'paused';

  export interface PausedEvent {
    conversation_id: ConversationId;
    message: Message;
    message_id: MessageId;
    reason: Reason;
    resume_at?: ResumeAt;
    seq: Seq;
    task_id?: TaskId;
    type?: Type;
  }
}
export type PausedEvent = PausedEventSchema.PausedEvent;

export namespace PlanLimitResumeResultSchema {
  export type ResumedTaskIds = string[];

  export interface PlanLimitResumeResult {
    resumed_task_ids?: ResumedTaskIds;
  }
}
export type PlanLimitResumeResult = PlanLimitResumeResultSchema.PlanLimitResumeResult;

export namespace ProviderApiKeyRequestSchema {
  export type ApiKey = string;

  export interface ProviderApiKeyRequest {
    api_key: ApiKey;
  }
}
export type ProviderApiKeyRequest = ProviderApiKeyRequestSchema.ProviderApiKeyRequest;

export namespace ProviderEnabledRequestSchema {
  export type Enabled = boolean;

  export interface ProviderEnabledRequest {
    enabled: Enabled;
  }
}
export type ProviderEnabledRequest = ProviderEnabledRequestSchema.ProviderEnabledRequest;

export namespace ProviderKeyStatusSchema {
  export type KeySaved = boolean;
  export type Provider = 'openai_key' | 'anthropic_key' | 'openrouter';

  export interface ProviderKeyStatus {
    key_saved: KeySaved;
    provider: Provider;
  }
}
export type ProviderKeyStatus = ProviderKeyStatusSchema.ProviderKeyStatus;

export namespace ProviderOptInSchema {
  export type Configured = boolean;
  export type CostWarning = string | null;
  export type Enabled = boolean;
  export type FeatureSwitches = string[];
  export type Label = string;
  export type Provider = 'chatgpt_plan' | 'openai_key' | 'anthropic_key' | 'openrouter' | 'local';

  export interface ProviderOptIn {
    configured?: Configured;
    cost_warning?: CostWarning;
    enabled: Enabled;
    feature_switches?: FeatureSwitches;
    label: Label;
    provider: Provider;
  }
}
export type ProviderOptIn = ProviderOptInSchema.ProviderOptIn;

export namespace ProviderSpendSchema {
  export type CapSet = boolean;
  export type CapUsd = number;
  export type Month = string;
  export type Provider = 'openai_key' | 'anthropic_key' | 'openrouter';
  export type SpentUsd = number;

  export interface ProviderSpend {
    cap_set: CapSet;
    cap_usd: CapUsd;
    month: Month;
    provider: Provider;
    spent_usd: SpentUsd;
  }
}
export type ProviderSpend = ProviderSpendSchema.ProviderSpend;

export namespace ProviderSpendCapRequestSchema {
  export type CapUsd = number;

  export interface ProviderSpendCapRequest {
    cap_usd: CapUsd;
  }
}
export type ProviderSpendCapRequest = ProviderSpendCapRequestSchema.ProviderSpendCapRequest;

export namespace ProviderSpendListSchema {
  export type CapSet = boolean;
  export type CapUsd = number;
  export type Month = string;
  export type Provider = 'openai_key' | 'anthropic_key' | 'openrouter';
  export type SpentUsd = number;
  export type Providers = ProviderSpend[];

  export interface ProviderSpendList {
    providers: Providers;
  }
  export interface ProviderSpend {
    cap_set: CapSet;
    cap_usd: CapUsd;
    month: Month;
    provider: Provider;
    spent_usd: SpentUsd;
  }
}
export type ProviderSpendList = ProviderSpendListSchema.ProviderSpendList;

export namespace RuleSchema {
  export type Action = string;
  export type Behavior = 'auto' | 'auto_if_preapproved' | 'ask' | 'handoff';
  export type CoreDeny = boolean;
  export type CreatedAt = string;
  export type CreatedFromApprovalId = string | null;
  export type Description = string | null;
  export type Enabled = boolean;
  export type Id = string;
  export type Locked = boolean;
  export type Name = string;

  export interface Rule {
    action: Action;
    behavior: Behavior;
    core_deny?: CoreDeny;
    created_at: CreatedAt;
    created_from_approval_id?: CreatedFromApprovalId;
    description?: Description;
    enabled?: Enabled;
    id: Id;
    locked?: Locked;
    name: Name;
  }
}
export type Rule = RuleSchema.Rule;

export namespace RuleCreateRequestSchema {
  export type Action = string;
  export type Behavior = 'auto' | 'auto_if_preapproved' | 'ask' | 'handoff';
  export type Description = string | null;
  export type Name = string;

  export interface RuleCreateRequest {
    action: Action;
    behavior: Behavior;
    description?: Description;
    name: Name;
  }
}
export type RuleCreateRequest = RuleCreateRequestSchema.RuleCreateRequest;

export namespace RuleListSchema {
  export type Action = string;
  export type Behavior = 'auto' | 'auto_if_preapproved' | 'ask' | 'handoff';
  export type CoreDeny = boolean;
  export type CreatedAt = string;
  export type CreatedFromApprovalId = string | null;
  export type Description = string | null;
  export type Enabled = boolean;
  export type Id = string;
  export type Locked = boolean;
  export type Name = string;
  export type Rules = Rule[];

  export interface RuleList {
    rules: Rules;
  }
  export interface Rule {
    action: Action;
    behavior: Behavior;
    core_deny?: CoreDeny;
    created_at: CreatedAt;
    created_from_approval_id?: CreatedFromApprovalId;
    description?: Description;
    enabled?: Enabled;
    id: Id;
    locked?: Locked;
    name: Name;
  }
}
export type RuleList = RuleListSchema.RuleList;

export namespace RuleUpdateRequestSchema {
  export type Behavior = ('auto' | 'auto_if_preapproved' | 'ask' | 'handoff') | null;
  export type Description = string | null;
  export type Enabled = boolean | null;
  export type Name = string | null;

  export interface RuleUpdateRequest {
    behavior?: Behavior;
    description?: Description;
    enabled?: Enabled;
    name?: Name;
  }
}
export type RuleUpdateRequest = RuleUpdateRequestSchema.RuleUpdateRequest;

export namespace SettingsSchema {
  export type AutoTopTier = boolean;
  export type Enabled = boolean;
  export type OnlyWhilePluggedIn = boolean;
  export type Configured = boolean;
  export type CostWarning = string | null;
  export type Enabled1 = boolean;
  export type FeatureSwitches = string[];
  export type Label = string;
  export type Provider = 'chatgpt_plan' | 'openai_key' | 'anthropic_key' | 'openrouter' | 'local';
  export type Providers = ProviderOptIn[];
  export type Enabled2 = boolean;
  export type End = string;
  export type Start = string;
  export type Timezone = string;
  export type StylePreset = 'concise' | 'warm' | 'formal' | 'playful';
  export type Effort = ('low' | 'medium' | 'high') | null;
  export type JobType = string;
  export type Tier = 'luna' | 'terra' | 'sol';
  export type TierOverrides = ModelTierOverride[];

  export interface Settings {
    auto_top_tier?: AutoTopTier;
    keep_awake: KeepAwakeSettings;
    providers: Providers;
    quiet_hours: QuietHoursSettings;
    style_preset: StylePreset;
    tier_overrides: TierOverrides;
  }
  export interface KeepAwakeSettings {
    enabled: Enabled;
    only_while_plugged_in?: OnlyWhilePluggedIn;
  }
  export interface ProviderOptIn {
    configured?: Configured;
    cost_warning?: CostWarning;
    enabled: Enabled1;
    feature_switches?: FeatureSwitches;
    label: Label;
    provider: Provider;
  }
  export interface QuietHoursSettings {
    enabled: Enabled2;
    end: End;
    start: Start;
    timezone: Timezone;
  }
  export interface ModelTierOverride {
    effort?: Effort;
    job_type: JobType;
    tier: Tier;
  }
}
export type Settings = SettingsSchema.Settings;

export namespace SettingsUpdateRequestSchema {
  export type AutoTopTier = boolean | null;
  export type Enabled = boolean;
  export type OnlyWhilePluggedIn = boolean;
  export type ProviderOptIns = {
    [k: string]: boolean;
  } | null;
  export type Enabled1 = boolean;
  export type End = string;
  export type Start = string;
  export type Timezone = string;
  export type StylePreset = ('concise' | 'warm' | 'formal' | 'playful') | null;
  export type TierOverrides = ModelTierOverride[] | null;
  export type Effort = ('low' | 'medium' | 'high') | null;
  export type JobType = string;
  export type Tier = 'luna' | 'terra' | 'sol';

  export interface SettingsUpdateRequest {
    auto_top_tier?: AutoTopTier;
    keep_awake?: KeepAwakeSettings | null;
    provider_opt_ins?: ProviderOptIns;
    quiet_hours?: QuietHoursSettings | null;
    style_preset?: StylePreset;
    tier_overrides?: TierOverrides;
  }
  export interface KeepAwakeSettings {
    enabled: Enabled;
    only_while_plugged_in?: OnlyWhilePluggedIn;
  }
  export interface QuietHoursSettings {
    enabled: Enabled1;
    end: End;
    start: Start;
    timezone: Timezone;
  }
  export interface ModelTierOverride {
    effort?: Effort;
    job_type: JobType;
    tier: Tier;
  }
}
export type SettingsUpdateRequest = SettingsUpdateRequestSchema.SettingsUpdateRequest;

export namespace StreamEventSchema {
  export type StreamEvent =
    | MessageStartedEvent
    | TextDeltaEvent
    | ToolCallEvent
    | ApprovalRequiredEvent
    | CompletedEvent
    | ErrorEvent
    | PausedEvent;
  export type ConversationId = string;
  export type MessageId = string;
  export type Seq = number;
  export type Type = 'message_started';
  export type ConversationId1 = string;
  export type MessageId1 = string;
  export type Seq1 = number;
  export type Text = string;
  export type Type1 = 'text_delta';
  export type CallId = string;
  export type Name = string;
  export type Status = 'running' | 'ok' | 'error' | 'needs_approval' | 'denied';
  export type Summary = string;
  export type ConversationId2 = string;
  export type MessageId2 = string;
  export type Seq2 = number;
  export type Type2 = 'tool_call';
  export type Action = string;
  export type ConversationId3 = string | null;
  export type CreatedAt = string;
  export type DecidedAt = string | null;
  export type ExpiresAt = string | null;
  export type Id = string;
  export type Preview = string;
  export type ReviewNote = string | null;
  export type ReviewVerdict = ('ok' | 'concern' | 'block') | null;
  export type RuleSuggestion = string | null;
  export type Status1 = 'pending' | 'approved' | 'edited' | 'denied' | 'expired';
  export type Title = string;
  export type ConversationId4 = string;
  export type MessageId3 = string;
  export type Seq3 = number;
  export type Type3 = 'approval_required';
  export type ConversationId5 = string;
  export type MessageId4 = string;
  export type Seq4 = number;
  export type Text1 = string;
  export type Type4 = 'completed';
  export type CachedInputTokens = number;
  export type Credits = number;
  export type Effort = 'low' | 'medium' | 'high';
  export type InputTokens = number;
  export type Model = string;
  export type OutputTokens = number;
  export type Code = string;
  export type ConversationId6 = string;
  export type Message = string;
  export type MessageId5 = string;
  export type Retryable = boolean;
  export type Seq5 = number;
  export type Type5 = 'error';
  export type ConversationId7 = string;
  export type Message1 = string;
  export type MessageId6 = string;
  export type Reason = 'user' | 'task_budget' | 'daily_budget' | 'rate_limited' | 'anomaly' | 'top_tier_approval';
  export type ResumeAt = string | null;
  export type Seq6 = number;
  export type TaskId = string | null;
  export type Type6 = 'paused';

  export interface MessageStartedEvent {
    conversation_id: ConversationId;
    message_id: MessageId;
    seq: Seq;
    type?: Type;
  }
  export interface TextDeltaEvent {
    conversation_id: ConversationId1;
    message_id: MessageId1;
    seq: Seq1;
    text: Text;
    type?: Type1;
  }
  export interface ToolCallEvent {
    call: ToolCallRecord;
    conversation_id: ConversationId2;
    message_id: MessageId2;
    seq: Seq2;
    type?: Type2;
  }
  export interface ToolCallRecord {
    call_id: CallId;
    name: Name;
    status: Status;
    summary: Summary;
  }
  export interface ApprovalRequiredEvent {
    approval: ApprovalItem;
    conversation_id: ConversationId4;
    message_id: MessageId3;
    seq: Seq3;
    type?: Type3;
  }
  export interface ApprovalItem {
    action: Action;
    conversation_id?: ConversationId3;
    created_at: CreatedAt;
    decided_at?: DecidedAt;
    expires_at?: ExpiresAt;
    id: Id;
    payload: Payload;
    preview: Preview;
    review_note?: ReviewNote;
    review_verdict?: ReviewVerdict;
    rule_suggestion?: RuleSuggestion;
    status: Status1;
    title: Title;
  }
  export interface Payload {
    [k: string]: string;
  }
  export interface CompletedEvent {
    conversation_id: ConversationId5;
    message_id: MessageId4;
    seq: Seq4;
    text: Text1;
    type?: Type4;
    usage: UsageStamp;
  }
  export interface UsageStamp {
    cached_input_tokens?: CachedInputTokens;
    credits: Credits;
    effort: Effort;
    input_tokens?: InputTokens;
    model: Model;
    output_tokens?: OutputTokens;
  }
  export interface ErrorEvent {
    code: Code;
    conversation_id: ConversationId6;
    message: Message;
    message_id: MessageId5;
    retryable?: Retryable;
    seq: Seq5;
    type?: Type5;
  }
  export interface PausedEvent {
    conversation_id: ConversationId7;
    message: Message1;
    message_id: MessageId6;
    reason: Reason;
    resume_at?: ResumeAt;
    seq: Seq6;
    task_id?: TaskId;
    type?: Type6;
  }
}
export type StreamEvent = StreamEventSchema.StreamEvent;

export namespace TaskActionResultSchema {
  export type Message = string;
  export type State = string;
  export type TaskId = string;

  /**
   * The outcome of a user decision on a paused task (continue anyway, allow the top tier).
   */
  export interface TaskActionResult {
    message?: Message;
    state: State;
    task_id: TaskId;
  }
}
export type TaskActionResult = TaskActionResultSchema.TaskActionResult;

export namespace TextDeltaEventSchema {
  export type ConversationId = string;
  export type MessageId = string;
  export type Seq = number;
  export type Text = string;
  export type Type = 'text_delta';

  export interface TextDeltaEvent {
    conversation_id: ConversationId;
    message_id: MessageId;
    seq: Seq;
    text: Text;
    type?: Type;
  }
}
export type TextDeltaEvent = TextDeltaEventSchema.TextDeltaEvent;

export namespace ToolCallEventSchema {
  export type CallId = string;
  export type Name = string;
  export type Status = 'running' | 'ok' | 'error' | 'needs_approval' | 'denied';
  export type Summary = string;
  export type ConversationId = string;
  export type MessageId = string;
  export type Seq = number;
  export type Type = 'tool_call';

  export interface ToolCallEvent {
    call: ToolCallRecord;
    conversation_id: ConversationId;
    message_id: MessageId;
    seq: Seq;
    type?: Type;
  }
  export interface ToolCallRecord {
    call_id: CallId;
    name: Name;
    status: Status;
    summary: Summary;
  }
}
export type ToolCallEvent = ToolCallEventSchema.ToolCallEvent;

export namespace UsageBudgetsSchema {
  export type DailyCredits = number | null;
  export type DailyHardStop = boolean;
  export type TaskCredits = number | null;

  export interface UsageBudgets {
    daily_credits?: DailyCredits;
    daily_hard_stop?: DailyHardStop;
    task_credits?: TaskCredits;
  }
}
export type UsageBudgets = UsageBudgetsSchema.UsageBudgets;

export namespace UsageQuerySchema {
  export type Days = number;

  export interface UsageQuery {
    days?: Days;
  }
}
export type UsageQuery = UsageQuerySchema.UsageQuery;

export namespace UsageSummarySchema {
  export type DailyCredits = number | null;
  export type DailyHardStop = boolean;
  export type TaskCredits = number | null;
  export type Credits = number;
  export type Day = string;
  export type ByDay = UsageByDay[];
  export type Credits1 = number;
  export type JobType = string;
  export type ByJobType = UsageByJobType[];
  export type Credits2 = number;
  export type TaskId = string;
  export type Title = string;
  export type ByTask = UsageByTask[];
  export type DailyBudgetRemaining = number | null;
  export type Days = number;
  export type ManageUsageUrl = string;
  export type PausedForBudget = boolean;
  export type PlanLabel = string;
  export type TodayCredits = number;
  export type TotalCredits = number;

  export interface UsageSummary {
    budgets: UsageBudgets;
    by_day: ByDay;
    by_job_type: ByJobType;
    by_task: ByTask;
    daily_budget_remaining?: DailyBudgetRemaining;
    days: Days;
    manage_usage_url: ManageUsageUrl;
    paused_for_budget?: PausedForBudget;
    plan_label: PlanLabel;
    today_credits: TodayCredits;
    total_credits: TotalCredits;
  }
  export interface UsageBudgets {
    daily_credits?: DailyCredits;
    daily_hard_stop?: DailyHardStop;
    task_credits?: TaskCredits;
  }
  export interface UsageByDay {
    credits: Credits;
    day: Day;
  }
  export interface UsageByJobType {
    credits: Credits1;
    job_type: JobType;
  }
  export interface UsageByTask {
    credits: Credits2;
    task_id: TaskId;
    title: Title;
  }
}
export type UsageSummary = UsageSummarySchema.UsageSummary;

export namespace VersionResponseSchema {
  export type ApiVersion = string;
  export type Build = string | null;
  export type DaemonVersion = string;
  export type SchemaVersion = number;

  export interface VersionResponse {
    api_version: ApiVersion;
    build?: Build;
    daemon_version: DaemonVersion;
    schema_version: SchemaVersion;
  }
}
export type VersionResponse = VersionResponseSchema.VersionResponse;
