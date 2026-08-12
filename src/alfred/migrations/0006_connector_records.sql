CREATE TABLE connector_records (
    connector TEXT NOT NULL,
    account TEXT NOT NULL,
    record_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    PRIMARY KEY (connector, account, record_type, record_id)
);

CREATE INDEX connector_records_active_idx
    ON connector_records(connector, account, record_type, active, observed_at DESC);
