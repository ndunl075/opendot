INSERT INTO type_registry (name, description, confirmed) VALUES
    ('calendar', 'A calendar containing durable events', 1)
ON CONFLICT(name) DO NOTHING;

INSERT INTO relation_registry (
    predicate, description, default_kind, default_cardinality, confirmed
) VALUES
    ('uses_calendar', 'The owner uses a calendar', 'state', 'multi', 1),
    ('takes_course', 'The owner takes or took a course', 'state', 'multi', 1)
ON CONFLICT(predicate) DO NOTHING;

CREATE TABLE historical_group_entities (
    group_key TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL UNIQUE REFERENCES entities(id),
    source_fingerprint TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE historical_memory_items (
    stable_key TEXT PRIMARY KEY,
    source_fingerprint TEXT NOT NULL,
    source_event_id TEXT NOT NULL REFERENCES events(id),
    memory_id TEXT NOT NULL REFERENCES memories(id),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    updated_at TEXT NOT NULL
);

CREATE INDEX historical_memory_items_active_idx
    ON historical_memory_items(active, updated_at);

CREATE TABLE historical_memory_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    rollup_fingerprint TEXT NOT NULL,
    item_count INTEGER NOT NULL CHECK (item_count >= 0),
    generated_at TEXT NOT NULL
);
