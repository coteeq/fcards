"""SQLite connections, schema bootstrap, and explicit write transactions."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from flask import current_app, g

from .search import matches_search
from .german import classification

SCHEMA_VERSION = 3


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
    """Serialize writers, including read/compare/write review finalization."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise


def initialize():
    db = get_db()
    db.execute("PRAGMA journal_mode = WAL")
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
            elif version == 0:
                tables = db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if tables:
                    raise RuntimeError("Refusing to initialize a nonempty, unversioned database.")
                # executescript() commits implicitly; keep DDL inside our writer lock.
                statement = ""
                for line in Path(__file__).with_name("schema.sql").read_text().splitlines(True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        db.execute(statement)
                        statement = ""
                if statement.strip():
                    raise RuntimeError("Incomplete schema statement")
            else:
                raise RuntimeError(
                    f"Unsupported database version {version}; expected {SCHEMA_VERSION}. "
                    "An explicit migration is required."
                )
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("Database migration failed its foreign-key check.")
    finally:
        db.execute("PRAGMA foreign_keys = ON")


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
