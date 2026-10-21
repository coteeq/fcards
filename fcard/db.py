"""SQLite connections, schema bootstrap, and explicit write transactions."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time

from flask import current_app, g

from .search import matches_search
from .german import classification

SCHEMA_VERSION = 4


def get_db():
    if "db" not in g:
        db = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.create_function("word_matches", 2, matches_search, deterministic=True)
        g.db = db
    return g.db


def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def transaction(db):
    """Serialize writers, including revision checks and atomic lesson completion."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise


def initialize():
    db = get_db()
    # Changing a brand-new database to WAL can report SQLITE_BUSY immediately
    # during concurrent startup, even with busy_timeout configured.
    deadline = time.monotonic() + 10
    while True:
        try:
            db.execute("PRAGMA journal_mode = WAL")
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)
    # Rebuilding questions must not cascade into progress/history. SQLite requires
    # this connection-local switch outside the transaction. All upgrades are serialized
    # and checked for FK violations before commit; other connections keep FK enforcement.
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        with transaction(db):
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == SCHEMA_VERSION:
                return
            if version == 2:
                migrate_v2_to_v3(db)
                version = 3
            if version == 3:
                execute_sql(db, Path(__file__).resolve().parent.parent / 'migrations/004_lessons.sql')
            elif version == 0:
                tables = db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if tables:
                    raise RuntimeError("Refusing to initialize a nonempty, unversioned database.")
                # executescript() commits implicitly; keep DDL inside our writer lock.
                execute_sql(db, Path(__file__).with_name("schema.sql"))
            else:
                raise RuntimeError(
                    f"Unsupported database version {version}; expected {SCHEMA_VERSION}. "
                    "An explicit migration is required."
                )
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("Database migration failed its foreign-key check.")
    finally:
        db.execute("PRAGMA foreign_keys = ON")


def execute_sql(db, path):
    """Unlike executescript, keep every statement in the caller\'s transaction."""
    statement = ""
    for line in path.read_text().splitlines(True):
        statement += line
        if sqlite3.complete_statement(statement):
            db.execute(statement)
            statement = ""
    if statement.strip():
        raise RuntimeError("Incomplete schema statement")


def migrate_v2_to_v3(db):
    """Preserve every old ID/schedule/log; add classification and new gender questions."""
    db.execute(
        "ALTER TABLE cards ADD COLUMN word_type TEXT "
        "CHECK (word_type IN ('adjective', 'verb', 'noun', 'phrase'))"
    )
    db.execute(
        "ALTER TABLE cards ADD COLUMN gender TEXT "
        "CHECK (gender IN ('masculine', 'feminine', 'neuter')) "
        "CHECK (gender IS NULL OR (word_type IS NOT NULL AND word_type = 'noun'))"
    )
    db.execute(
        """CREATE TABLE questions_v3 (
            id INTEGER PRIMARY KEY,
            card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (
                kind IN ('front_to_back', 'back_to_front', 'meta', 'gender')),
            UNIQUE (card_id, kind)
        )"""
    )
    db.execute("INSERT INTO questions_v3 SELECT id, card_id, kind FROM questions")
    db.execute("DROP TABLE questions")
    db.execute("ALTER TABLE questions_v3 RENAME TO questions")
    backs = db.execute(
        """SELECT f.card_id, f.text FROM card_forms f WHERE f.side = 'back'
           AND f.position = (SELECT MIN(first.position) FROM card_forms first
                             WHERE first.card_id = f.card_id AND first.side = 'back')"""
    ).fetchall()
    for row in backs:
        word_type, gender = classification(row["text"])
        db.execute("UPDATE cards SET word_type = ?, gender = ? WHERE id = ?",
                   (word_type, gender, row["card_id"]))
        if word_type == "noun" and gender:
            db.execute("INSERT INTO questions (card_id, kind) VALUES (?, 'gender')",
                       (row["card_id"],))
    db.execute("PRAGMA user_version = 3")
