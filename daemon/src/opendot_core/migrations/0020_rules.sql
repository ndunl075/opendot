CREATE TABLE rules (
    id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT '*',
    target TEXT,
    max_sensitivity TEXT NOT NULL DEFAULT 'secret'
        CHECK (max_sensitivity IN ('public', 'personal', 'sensitive', 'secret')),
    behavior TEXT NOT NULL CHECK (behavior IN ('auto', 'auto_if_preapproved', 'ask', 'handoff')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    note TEXT,
    max_cost_credits REAL
);

CREATE INDEX rules_tool_idx ON rules(tool);
