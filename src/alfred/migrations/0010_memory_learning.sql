CREATE TABLE memory_learning_runs (
    source_event_id TEXT PRIMARY KEY REFERENCES events(id),
    extractor_version TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('processed', 'error')),
    proposals INTEGER NOT NULL DEFAULT 0 CHECK (proposals >= 0),
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE memory_learning_candidates (
    normalized_key TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL UNIQUE REFERENCES memories(id),
    observation_count INTEGER NOT NULL DEFAULT 1 CHECK (observation_count >= 1),
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL
);

CREATE TABLE memory_learning_observations (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memories(id),
    source_event_id TEXT NOT NULL REFERENCES events(id),
    extractor_version TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    explicit INTEGER NOT NULL DEFAULT 0 CHECK (explicit IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE (memory_id, source_event_id)
);

CREATE INDEX memory_learning_observations_memory_idx
    ON memory_learning_observations(memory_id, created_at);

CREATE TABLE memory_retrieval_feedback (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memories(id),
    query TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('relevant', 'irrelevant', 'incorrect')),
    source_event_id TEXT REFERENCES events(id),
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX memory_retrieval_feedback_memory_idx
    ON memory_retrieval_feedback(memory_id, created_at);
