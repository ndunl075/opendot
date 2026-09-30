CREATE TABLE action_receipts (
    idempotency_key TEXT PRIMARY KEY,
    connector TEXT NOT NULL,
    action_type TEXT NOT NULL,
    approval_id TEXT NOT NULL REFERENCES approvals(id),
    actor TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX action_receipts_approval_idx ON action_receipts(approval_id);
