CREATE TABLE type_registry (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE relation_registry (
    predicate TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    default_kind TEXT NOT NULL CHECK (default_kind IN ('state', 'event')),
    default_cardinality TEXT NOT NULL CHECK (default_cardinality IN ('single', 'multi')),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO type_registry (name, description, confirmed) VALUES
    ('self', 'The Alfred owner identity', 1),
    ('person', 'A person', 1),
    ('organization', 'An organization', 1),
    ('school', 'A school or university', 1),
    ('course', 'A course or class', 1),
    ('project', 'A durable project', 1),
    ('goal', 'A personal goal', 1),
    ('document', 'A durable document', 1),
    ('task', 'A task represented as a graph entity', 1),
    ('preference', 'A durable user preference', 1);

INSERT INTO relation_registry (predicate, description, default_kind, default_cardinality, confirmed) VALUES
    ('related_to', 'A general durable relationship', 'state', 'multi', 1),
    ('studies_at', 'A person studies at a school', 'state', 'single', 1),
    ('works_on', 'A person works on a project', 'state', 'multi', 1),
    ('member_of', 'A person belongs to an organization', 'state', 'multi', 1);

CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL REFERENCES type_registry(name),
    label TEXT NOT NULL CHECK (length(trim(label)) > 0),
    properties_json TEXT NOT NULL DEFAULT '{}',
    domains_json TEXT NOT NULL DEFAULT '[]',
    sensitivity TEXT NOT NULL DEFAULT 'personal'
        CHECK (sensitivity IN ('public', 'personal', 'sensitive', 'secret')),
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX entities_one_self_idx ON entities(entity_type) WHERE entity_type = 'self';
CREATE INDEX entities_type_label_idx ON entities(entity_type, label);

CREATE TABLE aliases (
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias TEXT NOT NULL CHECK (length(trim(alias)) > 0),
    source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    PRIMARY KEY (entity_id, alias)
);

CREATE TABLE relationships (
    id TEXT PRIMARY KEY,
    source_entity_id TEXT NOT NULL REFERENCES entities(id),
    predicate TEXT NOT NULL REFERENCES relation_registry(predicate),
    target_entity_id TEXT NOT NULL REFERENCES entities(id),
    relation_kind TEXT NOT NULL CHECK (relation_kind IN ('state', 'event')),
    cardinality TEXT NOT NULL CHECK (cardinality IN ('single', 'multi')),
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    domains_json TEXT NOT NULL DEFAULT '[]',
    sensitivity TEXT NOT NULL DEFAULT 'personal'
        CHECK (sensitivity IN ('public', 'personal', 'sensitive', 'secret')),
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE INDEX relationships_active_source_idx
    ON relationships(source_entity_id, predicate, relation_kind, cardinality)
    WHERE valid_to IS NULL;
CREATE INDEX relationships_active_target_idx ON relationships(target_entity_id) WHERE valid_to IS NULL;

CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    statement TEXT NOT NULL CHECK (length(trim(statement)) > 0),
    status TEXT NOT NULL CHECK (status IN ('candidate', 'confirmed', 'superseded', 'rejected', 'deleted')),
    source_event_id TEXT REFERENCES events(id),
    supersedes_memory_id TEXT REFERENCES memories(id),
    valid_from TEXT,
    valid_to TEXT,
    domains_json TEXT NOT NULL DEFAULT '[]',
    sensitivity TEXT NOT NULL DEFAULT 'personal'
        CHECK (sensitivity IN ('public', 'personal', 'sensitive', 'secret')),
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX memories_status_created_idx ON memories(status, created_at DESC);
CREATE INDEX memories_source_event_idx ON memories(source_event_id);

CREATE TABLE evidence (
    id TEXT PRIMARY KEY,
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('entity', 'relationship', 'memory')),
    subject_id TEXT NOT NULL,
    source_event_id TEXT REFERENCES events(id),
    document_reference TEXT,
    source_account TEXT,
    extraction_version TEXT,
    excerpt_hash TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX evidence_subject_idx ON evidence(subject_kind, subject_id);

CREATE TABLE memory_history (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memories(id),
    previous_status TEXT,
    next_status TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE entity_fts USING fts5(entity_id UNINDEXED, label, aliases);
CREATE VIRTUAL TABLE memory_fts USING fts5(memory_id UNINDEXED, statement);
