"""SQLite connections, schema bootstrap, and explicit write transactions."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from flask import current_app, g

SCHEMA_VERSION = 1


def get_db():
    if "db" not in g:
        db = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
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
    with transaction(db):
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version != 0:
            raise RuntimeError(
                f"Unsupported database version {version}; expected {SCHEMA_VERSION}. "
                "An explicit migration is required."
            )
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if tables:
            raise RuntimeError("Refusing to initialize a nonempty, unversioned database.")
        # executescript() implicitly commits; execute complete statements individually
        # so version checking and schema creation share the same writer lock.
        statement = ""
        for line in Path(__file__).with_name("schema.sql").read_text().splitlines(True):
            statement += line
            if sqlite3.complete_statement(statement):
                db.execute(statement)
                statement = ""
        if statement.strip():
            raise RuntimeError("Incomplete schema statement")
