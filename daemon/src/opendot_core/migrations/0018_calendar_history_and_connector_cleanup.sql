-- OpenDot M0: Alfred's "academic" rollups become Calendar-only "calendar"
-- rollups, and data from the removed Canvas and Google Health connectors is
-- dropped. Forward-only; earlier migrations are untouched.

-- 1. Rename the rollup tables (derived data, rebuilt on the next cycle).
DROP INDEX IF EXISTS academic_daily_rollups_day_idx;
DROP INDEX IF EXISTS academic_group_rollups_label_idx;

ALTER TABLE academic_daily_rollups RENAME TO calendar_daily_rollups;
ALTER TABLE academic_group_rollups RENAME TO calendar_group_rollups;
ALTER TABLE academic_rollup_state RENAME TO calendar_rollup_state;

CREATE INDEX calendar_daily_rollups_day_idx
    ON calendar_daily_rollups(day DESC, group_label);
CREATE INDEX calendar_group_rollups_label_idx
    ON calendar_group_rollups(group_label);

-- The rollups held Canvas items too. Clear them so they rebuild from Calendar.
DELETE FROM calendar_daily_rollups;
DELETE FROM calendar_group_rollups;
DELETE FROM calendar_rollup_state;
DELETE FROM historical_memory_state;

-- 2. Drop Canvas / Google Health / BrowserOS connector state.
DELETE FROM connector_records
WHERE connector IN ('canvas', 'canvas_ical', 'canvas_history', 'google_health', 'browseros');
DELETE FROM sync_state
WHERE connector IN ('canvas', 'canvas_ical', 'canvas_history', 'google_health', 'browseros');

-- 3. Drop the events those connectors wrote, and everything that cites them.
CREATE TEMP TABLE doomed_events AS
    SELECT id FROM events WHERE source IN ('canvas', 'canvas_ical', 'google_health', 'browseros');

CREATE TEMP TABLE doomed_entities AS
    SELECT entity_id AS id FROM historical_group_entities WHERE group_key LIKE 'canvas:%';

CREATE TEMP TABLE doomed_memories AS
    SELECT memory_id AS id FROM historical_memory_items WHERE stable_key LIKE 'canvas:%'
    UNION
    SELECT id FROM memories WHERE source_event_id IN (SELECT id FROM doomed_events);

DELETE FROM historical_memory_items
WHERE memory_id IN (SELECT id FROM doomed_memories)
   OR source_event_id IN (SELECT id FROM doomed_events);

UPDATE memories SET supersedes_memory_id = NULL
WHERE supersedes_memory_id IN (SELECT id FROM doomed_memories);

DELETE FROM memory_fts WHERE memory_id IN (SELECT id FROM doomed_memories);
DELETE FROM embeddings WHERE subject_kind = 'memory' AND subject_id IN (SELECT id FROM doomed_memories);
DELETE FROM memory_history WHERE memory_id IN (SELECT id FROM doomed_memories);
DELETE FROM memory_learning_candidates WHERE memory_id IN (SELECT id FROM doomed_memories);
DELETE FROM memory_learning_observations
WHERE memory_id IN (SELECT id FROM doomed_memories)
   OR source_event_id IN (SELECT id FROM doomed_events);
DELETE FROM memory_learning_runs WHERE source_event_id IN (SELECT id FROM doomed_events);
DELETE FROM memory_retrieval_feedback WHERE memory_id IN (SELECT id FROM doomed_memories);
UPDATE memory_retrieval_feedback SET source_event_id = NULL
WHERE source_event_id IN (SELECT id FROM doomed_events);
DELETE FROM evidence
WHERE (subject_kind = 'memory' AND subject_id IN (SELECT id FROM doomed_memories))
   OR (subject_kind = 'entity' AND subject_id IN (SELECT id FROM doomed_entities))
   OR source_event_id IN (SELECT id FROM doomed_events);
DELETE FROM memories WHERE id IN (SELECT id FROM doomed_memories);

DELETE FROM relationships
WHERE source_entity_id IN (SELECT id FROM doomed_entities)
   OR target_entity_id IN (SELECT id FROM doomed_entities);
DELETE FROM entity_fts WHERE entity_id IN (SELECT id FROM doomed_entities);
DELETE FROM historical_group_entities WHERE entity_id IN (SELECT id FROM doomed_entities);
DELETE FROM entities WHERE id IN (SELECT id FROM doomed_entities);

DELETE FROM documents WHERE event_id IN (SELECT id FROM doomed_events);
DELETE FROM tasks WHERE source_event_id IN (SELECT id FROM doomed_events);
DELETE FROM events WHERE id IN (SELECT id FROM doomed_events);

DROP TABLE doomed_events;
DROP TABLE doomed_entities;
DROP TABLE doomed_memories;
