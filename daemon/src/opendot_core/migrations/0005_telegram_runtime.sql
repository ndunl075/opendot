ALTER TABLE outbox ADD COLUMN last_error TEXT;

CREATE TABLE sync_state (
    connector TEXT NOT NULL,
    account TEXT NOT NULL,
    cursor TEXT,
    last_success_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (connector, account)
);
