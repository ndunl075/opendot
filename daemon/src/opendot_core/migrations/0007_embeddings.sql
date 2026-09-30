CREATE TABLE embeddings (
    id TEXT PRIMARY KEY,
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('memory', 'entity')),
    subject_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    dim INTEGER NOT NULL CHECK (dim > 0),
    vector BLOB NOT NULL,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX embeddings_subject_model_idx
    ON embeddings(subject_kind, subject_id, model_name);
CREATE INDEX embeddings_model_idx ON embeddings(model_name);
