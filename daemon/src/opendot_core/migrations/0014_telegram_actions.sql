CREATE TABLE telegram_action_links (
    approval_id TEXT PRIMARY KEY REFERENCES approvals(id),
    response_update_id TEXT NOT NULL REFERENCES response_context(response_update_id),
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX telegram_action_links_response_idx
    ON telegram_action_links(response_update_id);

CREATE TABLE telegram_action_intents (
    approval_id TEXT PRIMARY KEY REFERENCES telegram_action_links(approval_id),
    callback_query_id TEXT NOT NULL UNIQUE,
    feedback_update_id TEXT NOT NULL UNIQUE,
    decision TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'running', 'completed', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX telegram_action_intents_work_idx
    ON telegram_action_intents(state, next_attempt_at, created_at);
