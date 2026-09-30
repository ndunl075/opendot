CREATE TABLE credit_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    job_type TEXT NOT NULL,
    model TEXT NOT NULL,
    effort TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    credits REAL NOT NULL,
    rate_known INTEGER NOT NULL CHECK (rate_known IN (0, 1)),
    day TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX credit_records_task_idx ON credit_records(task_id, id);
CREATE INDEX credit_records_day_idx ON credit_records(day);
CREATE INDEX credit_records_job_type_idx ON credit_records(job_type);

CREATE TABLE usage_budgets (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    task_credits REAL NOT NULL CHECK (task_credits >= 0),
    daily_credits REAL NOT NULL CHECK (daily_credits >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE task_budget_overruns (
    task_id TEXT PRIMARY KEY,
    allowed_at TEXT NOT NULL
);
