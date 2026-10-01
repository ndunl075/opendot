CREATE TABLE events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT,
    occurred_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    content TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    sensitivity TEXT NOT NULL DEFAULT 'personal'
        CHECK (sensitivity IN ('public', 'personal', 'sensitive', 'secret')),
    content_hash TEXT NOT NULL,
    UNIQUE (source, external_id)
);

CREATE TABLE tool_runs (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    occurred_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    client TEXT NOT NULL,
    tool TEXT NOT NULL,
    outcome TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    correlation_id TEXT,
    previous_hash TEXT,
    record_hash TEXT NOT NULL UNIQUE
);

CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    requested_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action_type TEXT NOT NULL,
    preview_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'approved', 'rejected', 'expired', 'consumed')),
    consumed_at TEXT,
    tool_run_id TEXT REFERENCES tool_runs(id)
);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    schedule_json TEXT NOT NULL,
    next_run_at TEXT,
    state TEXT NOT NULL CHECK (state IN ('active', 'paused', 'completed', 'failed')),
    payload_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE outbox (
    id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(id),
    destination TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('pending', 'sending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT
);

CREATE TRIGGER tool_runs_prevent_update
BEFORE UPDATE ON tool_runs
BEGIN
    SELECT RAISE(ABORT, 'tool_runs is append-only');
END;

CREATE TRIGGER tool_runs_prevent_delete
BEFORE DELETE ON tool_runs
BEGIN
    SELECT RAISE(ABORT, 'tool_runs is append-only');
END;

CREATE INDEX events_occurred_at_idx ON events(occurred_at DESC);
CREATE INDEX events_source_external_id_idx ON events(source, external_id);
CREATE INDEX events_source_content_hash_idx ON events(source, content_hash);
CREATE INDEX tool_runs_occurred_at_idx ON tool_runs(occurred_at DESC);
CREATE INDEX approvals_state_expires_at_idx ON approvals(state, expires_at);
CREATE INDEX jobs_next_run_at_idx ON jobs(state, next_run_at);
CREATE INDEX outbox_state_created_at_idx ON outbox(state, created_at);
