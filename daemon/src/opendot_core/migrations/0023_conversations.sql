-- OpenDot M4: persisted chat conversations (the UI's conversation list and history).
-- Forward-only. Rows are written by the API server as chat tasks run; the audit log is separate.

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE conversation_messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    task_id TEXT,
    tool_calls_json TEXT NOT NULL DEFAULT '[]',
    approval_id TEXT,
    usage_json TEXT
);

CREATE INDEX conversation_messages_conv_idx ON conversation_messages(conversation_id, seq);
CREATE INDEX conversations_updated_idx ON conversations(updated_at DESC);
