CREATE TABLE workflow_turns (
    turn_id TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'ok', 'error')),
    first_observed_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE workflow_tool_observations (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL REFERENCES workflow_turns(turn_id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL CHECK (step_index >= 0),
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE (turn_id, step_index)
);

CREATE INDEX workflow_observations_turn_idx
    ON workflow_tool_observations(turn_id, step_index);

CREATE TABLE workflow_skill_versions (
    id TEXT PRIMARY KEY,
    pattern_signature TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    state TEXT NOT NULL
        CHECK (state IN ('draft', 'pending', 'accepted', 'active', 'rejected', 'superseded')),
    definition_json TEXT NOT NULL,
    skill_markdown TEXT NOT NULL,
    diff_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL CHECK (occurrence_count >= 1),
    distinct_days INTEGER NOT NULL CHECK (distinct_days >= 1),
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    approval_id TEXT UNIQUE REFERENCES approvals(id),
    activated_path TEXT,
    created_at TEXT NOT NULL,
    activated_at TEXT,
    UNIQUE (skill_name, version)
);

CREATE INDEX workflow_skill_versions_state_idx
    ON workflow_skill_versions(state, created_at DESC);
CREATE INDEX workflow_skill_versions_pattern_idx
    ON workflow_skill_versions(pattern_signature, version DESC);
