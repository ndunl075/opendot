CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    state TEXT NOT NULL CHECK (state IN ('open', 'completed', 'cancelled')),
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high')),
    due_at TEXT,
    source_event_id TEXT NOT NULL REFERENCES events(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX tasks_state_due_at_idx ON tasks(state, due_at);
CREATE INDEX tasks_source_event_id_idx ON tasks(source_event_id);
CREATE INDEX jobs_kind_next_run_at_idx ON jobs(kind, state, next_run_at);
