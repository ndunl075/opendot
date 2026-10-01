ALTER TABLE approvals ADD COLUMN token_hash TEXT;
ALTER TABLE approvals ADD COLUMN approved_at TEXT;
ALTER TABLE approvals ADD COLUMN approved_by TEXT;

CREATE UNIQUE INDEX approvals_token_hash_idx ON approvals(token_hash) WHERE token_hash IS NOT NULL;

CREATE TABLE client_scopes (
    client_id TEXT PRIMARY KEY,
    allowed_sensitivities_json TEXT NOT NULL,
    allowed_tools_json TEXT NOT NULL,
    allow_write INTEGER NOT NULL DEFAULT 0 CHECK (allow_write IN (0, 1)),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
