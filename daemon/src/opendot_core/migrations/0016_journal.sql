CREATE TABLE mood_entries (
    id TEXT PRIMARY KEY,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    note TEXT,
    recorded_at TEXT NOT NULL
);

CREATE INDEX mood_entries_recorded_idx ON mood_entries(recorded_at DESC);

CREATE TABLE gratitude_entries (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX gratitude_entries_recorded_idx ON gratitude_entries(recorded_at DESC);
