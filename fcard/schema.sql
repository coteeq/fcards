CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE CHECK (length(trim(username)) BETWEEN 1 AND 80),
    created_at INTEGER NOT NULL
);

CREATE TABLE decks (
    name TEXT PRIMARY KEY NOT NULL CHECK (
        length(name) BETWEEN 1 AND 120
        AND substr(name, 1, 1) GLOB '[A-Za-z_]'
        AND name NOT GLOB '*[^A-Za-z0-9_]*'
    ),
    description TEXT NOT NULL DEFAULT '',
    author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL
);

CREATE TABLE cards (
    id INTEGER PRIMARY KEY,
    deck_name TEXT NOT NULL REFERENCES decks(name) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    meta TEXT NOT NULL DEFAULT '' CHECK (length(meta) <= 500),
    word_type TEXT CHECK (word_type IN ('adjective', 'verb', 'noun', 'phrase')),
    gender TEXT CHECK (gender IN ('masculine', 'feminine', 'neuter'))
        CHECK (gender IS NULL OR (word_type IS NOT NULL AND word_type = 'noun')),
    created_at INTEGER NOT NULL
);
CREATE INDEX cards_deck ON cards(deck_name);

CREATE TABLE card_forms (
    id INTEGER PRIMARY KEY,
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    side TEXT NOT NULL CHECK (side IN ('front', 'back')),
    position INTEGER NOT NULL CHECK (position >= 0),
    text TEXT NOT NULL CHECK (length(trim(text)) BETWEEN 1 AND 500),
    description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 160),
    UNIQUE (card_id, side, position)
);

CREATE TABLE questions (
    id INTEGER PRIMARY KEY,
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('front_to_back', 'back_to_front', 'meta', 'gender')),
    UNIQUE (card_id, kind)
);

CREATE TABLE progress (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    algorithm TEXT NOT NULL,
    state_json TEXT NOT NULL CHECK (json_valid(state_json)),
    due_at INTEGER,
    last_reviewed_at INTEGER NOT NULL,
    reviews INTEGER NOT NULL CHECK (reviews >= 1),
    lapses INTEGER NOT NULL CHECK (lapses BETWEEN 0 AND reviews),
    PRIMARY KEY (user_id, question_id)
);
CREATE INDEX progress_due ON progress(user_id, due_at);

CREATE TABLE lessons (
    id TEXT PRIMARY KEY NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    deck_name TEXT NOT NULL REFERENCES decks(name) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    completed_at INTEGER,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
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
