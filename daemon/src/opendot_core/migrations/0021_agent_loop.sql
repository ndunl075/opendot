CREATE TABLE agent_tasks (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    job_type TEXT NOT NULL,
    message TEXT NOT NULL,
    chat_id INTEGER,
    preapproved_json TEXT NOT NULL DEFAULT '[]',
    tool_group_json TEXT NOT NULL DEFAULT '[]',
    prefix_hash TEXT,
    summary TEXT NOT NULL DEFAULT '',
    history_start INTEGER NOT NULL DEFAULT 0,
    escalate_from TEXT,
    top_tier_approved INTEGER NOT NULL DEFAULT 0 CHECK (top_tier_approved IN (0, 1)),
    tried_json TEXT NOT NULL DEFAULT '[]',
    detail TEXT NOT NULL DEFAULT '',
    final_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX agent_tasks_state_idx ON agent_tasks(state);

CREATE TABLE agent_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES agent_tasks(id),
    kind TEXT NOT NULL CHECK (kind IN ('model', 'tool', 'compact')),
    state TEXT NOT NULL CHECK (state IN ('pending', 'started', 'waiting', 'done', 'failed', 'skipped')),
    parent_step INTEGER,
    tier TEXT,
    model TEXT,
    effort TEXT,
    call_id TEXT,
    tool TEXT,
    arguments TEXT,
    behavior TEXT,
    approval_id TEXT,
    output_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX agent_steps_task_idx ON agent_steps(task_id, id);
CREATE UNIQUE INDEX agent_steps_tool_call_idx ON agent_steps(task_id, parent_step, call_id) WHERE kind = 'tool';
CREATE INDEX agent_steps_approval_idx ON agent_steps(approval_id);

CREATE TABLE agent_history (
    task_id TEXT NOT NULL REFERENCES agent_tasks(id),
    seq INTEGER NOT NULL,
    item_json TEXT NOT NULL,
    PRIMARY KEY (task_id, seq)
);

CREATE TABLE agent_control (
    key TEXT PRIMARY KEY CHECK (key IN ('plan_limit', 'kill_switch')),
    reason TEXT NOT NULL,
    since TEXT NOT NULL
);
