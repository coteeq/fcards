CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE CHECK (length(trim(username)) BETWEEN 1 AND 80),
    created_at INTEGER NOT NULL
);

CREATE TABLE decks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) BETWEEN 1 AND 120),
    description TEXT NOT NULL DEFAULT '',
    author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL
);

CREATE TABLE cards (
    id INTEGER PRIMARY KEY,
    deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    meta TEXT NOT NULL DEFAULT '' CHECK (length(meta) <= 500),
    created_at INTEGER NOT NULL
);
CREATE INDEX cards_deck ON cards(deck_id);

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
    kind TEXT NOT NULL CHECK (kind IN ('front_to_back', 'back_to_front', 'meta')),
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

CREATE TABLE review_log (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    reviewed_at INTEGER NOT NULL,
    auto_verdict TEXT NOT NULL CHECK (auto_verdict IN ('correct', 'incorrect')),
    rating TEXT NOT NULL,
    control TEXT,
    algorithm TEXT NOT NULL,
    before_state_json TEXT NOT NULL CHECK (json_valid(before_state_json)),
    after_state_json TEXT NOT NULL CHECK (json_valid(after_state_json)),
    due_at INTEGER
);
CREATE INDEX review_log_user_time ON review_log(user_id, reviewed_at, id);

PRAGMA user_version = 1;
