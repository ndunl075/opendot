CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id),
    uri TEXT NOT NULL,
    mime_type TEXT,
    checksum TEXT NOT NULL,
    retention_policy TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX documents_event_idx ON documents(event_id);
CREATE INDEX documents_checksum_idx ON documents(checksum);
