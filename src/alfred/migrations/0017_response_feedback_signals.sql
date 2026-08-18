-- Feedback is no longer one button tap per answer. A response can now be
-- judged by more than one independent signal (the owner's own next message,
-- and Alfred's check of the context it answered from), so the single-row-per
-- -response shape and the mandatory callback columns both have to go. SQLite
-- cannot drop a UNIQUE constraint or relax NOT NULL in place, so the table is
-- rebuilt and copied.
CREATE TABLE response_feedback_rebuilt (
    id TEXT PRIMARY KEY,
    -- 'button' is an explicit tap, 'reply' is inferred from what the owner
    -- said next, 'coverage' is Alfred grading its own context pack.
    signal TEXT NOT NULL CHECK (signal IN ('button', 'reply', 'coverage')),
    -- Nullable now: only a tapped button has a Telegram callback, and only a
    -- signal carried by an inbound update has an update id.
    callback_query_id TEXT UNIQUE,
    feedback_update_id TEXT UNIQUE,
    response_update_id TEXT NOT NULL REFERENCES response_context(response_update_id),
    outcome TEXT NOT NULL CHECK (outcome IN ('helpful', 'missing_context', 'wrong_context')),
    -- Which named rule fired, never the text that matched it. This table stays
    -- content-free: no prompt, answer, or message text is stored here.
    rule TEXT,
    created_at TEXT NOT NULL,
    -- One verdict per response *per signal*: a detector cannot vote twice, but
    -- an inferred verdict never blocks a later explicit one either.
    UNIQUE (response_update_id, signal)
);

INSERT INTO response_feedback_rebuilt (
    id, signal, callback_query_id, feedback_update_id,
    response_update_id, outcome, rule, created_at
)
SELECT
    id, 'button', callback_query_id, feedback_update_id,
    response_update_id, outcome, NULL, created_at
FROM response_feedback;

DROP TABLE response_feedback;
ALTER TABLE response_feedback_rebuilt RENAME TO response_feedback;

CREATE INDEX response_feedback_created_idx
    ON response_feedback(created_at DESC);
CREATE INDEX response_feedback_response_idx
    ON response_feedback(response_update_id);
