CREATE TABLE academic_daily_rollups (
    day TEXT NOT NULL,
    group_key TEXT NOT NULL,
    group_label TEXT NOT NULL,
    items_json TEXT NOT NULL,
    search_text TEXT NOT NULL,
    item_count INTEGER NOT NULL CHECK (item_count >= 0),
    generated_at TEXT NOT NULL,
    PRIMARY KEY (day, group_key)
);

CREATE INDEX academic_daily_rollups_day_idx
    ON academic_daily_rollups(day DESC, group_label);

CREATE TABLE academic_group_rollups (
    group_key TEXT PRIMARY KEY,
    group_label TEXT NOT NULL,
    first_day TEXT NOT NULL,
    last_day TEXT NOT NULL,
    stats_json TEXT NOT NULL,
    search_text TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE INDEX academic_group_rollups_label_idx
    ON academic_group_rollups(group_label);

CREATE TABLE academic_rollup_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    source_fingerprint TEXT NOT NULL,
    source_event_count INTEGER NOT NULL CHECK (source_event_count >= 0),
    generated_at TEXT NOT NULL
);
