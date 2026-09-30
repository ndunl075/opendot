CREATE TABLE response_context (
    response_update_id TEXT PRIMARY KEY,
    sources_json TEXT NOT NULL DEFAULT '[]',
    freshness_json TEXT NOT NULL DEFAULT '{}',
    items_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE response_feedback (
    id TEXT PRIMARY KEY,
    callback_query_id TEXT NOT NULL UNIQUE,
    feedback_update_id TEXT NOT NULL UNIQUE,
    response_update_id TEXT NOT NULL UNIQUE REFERENCES response_context(response_update_id),
    outcome TEXT NOT NULL CHECK (outcome IN ('helpful', 'missing_context', 'wrong_context')),
    created_at TEXT NOT NULL
);

CREATE INDEX response_feedback_created_idx
    ON response_feedback(created_at DESC);
