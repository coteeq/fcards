-- v3 -> v4. Run inside BEGIN IMMEDIATE; see migrate.py or fcard/db.py.
-- This one-time upgrade deliberately discards per-answer history, not vocabulary
-- or scheduling progress. Lessons retain only final ratings, never raw answers or
-- automatic/corrected verdict provenance.
DROP TABLE IF EXISTS review_log;

CREATE TABLE lessons (
    id TEXT PRIMARY KEY NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    deck_name TEXT NOT NULL REFERENCES decks(name) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    completed_at INTEGER,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),˝
    state_json TEXT NOT NULL DEFAULT '{"cursor":0,"ratings":[]}' CHECK (json_valid(state_json)),
    skipped INTEGER NOT NULL DEFAULT 0 CHECK (skipped BETWEEN 0 AND 20)
);
CREATE INDEX lessons_unfinished ON lessons(user_id, completed_at, updated_at);

CREATE TABLE lesson_items (
    lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position BETWEEN 0 AND 19),
    question_id INTEGER REFERENCES questions(id) ON DELETE SET NULL,
    algorithm TEXT NOT NULL,
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
    PRIMARY KEY (lesson_id, position)
);
CREATE INDEX lesson_items_question ON lesson_items(question_id);

PRAGMA user_version = 4;
