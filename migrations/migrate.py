#!/usr/bin/env python3
"""Upgrade an existing fcard database, optionally backing it up first.

From fcards/: uv run --locked python migrations/migrate.py instance/fcard.sqlite3 \
    --backup /safe/path/fcard-before-v4.sqlite3
Stop all old app workers first. SQL and data migrations run in one transaction.
"""

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import Flask  # noqa: E402
from fcard.db import SCHEMA_VERSION, close_db, initialize  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--backup", type=Path, help="New backup file; never overwrites an existing file")
    args = parser.parse_args()
    database = args.database.resolve()
    if not database.is_file():
        parser.error("Database must be an existing file (refusing to create an empty replacement).")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
        old_version = source.execute("PRAGMA user_version").fetchone()[0]
        if old_version not in (2, 3, SCHEMA_VERSION):
            parser.error(f"Unsupported schema version {old_version}.")
        if args.backup:
            # Exclusive creation prevents overwriting an existing backup or the source.
            with args.backup.open("xb"):
                pass
            with closing(sqlite3.connect(args.backup)) as target:
                source.backup(target)
            print(f"Backup: {args.backup}")
    app = Flask(__name__)
    app.config["DATABASE"] = str(database)
    app.teardown_appcontext(close_db)
    with app.app_context():
        initialize()
    print(f"Schema {old_version} -> {SCHEMA_VERSION}; vocabulary and scheduling progress preserved.")


if __name__ == "__main__":
    main()
