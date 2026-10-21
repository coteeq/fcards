from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from flask import Flask

from fcard import create_app
from fcard.db import close_db, get_db, initialize, migrate_v2_to_v3, transaction


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp.name) / "old.sqlite3")
        with sqlite3.connect(self.database) as db:
            db.executescript((Path(__file__).parent / "fixtures/schema_v2.sql").read_text())
            db.execute("PRAGMA foreign_keys = ON")
            db.executemany("INSERT INTO users VALUES (?, ?, 123)", [(1, "Alice"), (2, "Bob")])
            db.execute("INSERT INTO decks VALUES ('German', 'shared', 1, 456)")
            backs = ["  der Morgen  ", "gehen", "guten Morgen", "groß", "die Katze", "das Haus",
                     "morning", "der gute Morgen", "der", "DER\tMorgen"]
            for index, back in enumerate(backs, 10):
                db.execute("INSERT INTO cards VALUES (?, 'German', 7, 'old metadata', 789)", (index,))
                for side, text in (("front", "der English" if index == 16 else "morning"), ("back", back)):
                    # Canonical is minimum position, not necessarily zero.
                    db.execute("INSERT INTO card_forms (card_id, side, position, text, description) "
                               "VALUES (?, ?, 4, ?, ' original description ')", (index, side, text))
                for kind in ("front_to_back", "back_to_front", "meta"):
                    db.execute("INSERT INTO questions (card_id, kind) VALUES (?, ?)", (index, kind))
            db.execute("INSERT INTO card_forms (card_id, side, position, text) "
                       "VALUES (10, 'back', 8, 'die Morgen')")
            for user in (1, 2):
                for question in (1, 3):
                    db.execute("INSERT INTO progress VALUES (?, ?, 'buckets-v1', ?, NULL, 111, 3, 1)",
                               (user, question, '{"bucket":4}'))
                    db.execute("INSERT INTO review_log (user_id, question_id, reviewed_at, auto_verdict, "
                               "rating, algorithm, before_state_json, after_state_json) "
                               "VALUES (?, ?, 111, 'correct', 'correct', 'buckets-v1', 'null', ?)",
                               (user, question, '{"bucket":4}'))
        self.before = self.snapshot()

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        with sqlite3.connect(self.database) as db:
            return {table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                    for table in ("users", "decks", "cards", "card_forms", "questions", "progress", "review_log")
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()}

    def upgrade(self):
        return create_app({"TESTING": True, "DATABASE": self.database, "SECRET_KEY": "test"})

    def test_upgrade_preserves_every_old_record_id_revision_and_schedule(self):
        app = self.upgrade()
        with app.app_context():
            db = get_db()
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            for table, rows in self.before.items():
                if table == "review_log":
                    self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name=\'review_log\'").fetchone())
                    continue
                if table == "cards":
                    query = "SELECT id, deck_name, revision, meta, created_at FROM cards ORDER BY rowid"
                elif table == "questions":
                    query = "SELECT * FROM questions WHERE kind != 'gender' ORDER BY rowid"
                else:
                    query = f"SELECT * FROM {table} ORDER BY rowid"
                self.assertEqual([tuple(row) for row in db.execute(query)], rows, table)
            classifications = [tuple(row) for row in db.execute(
                "SELECT word_type, gender FROM cards ORDER BY id")]
            self.assertEqual(classifications, [
                ("noun", "masculine"), ("verb", None), ("phrase", None), (None, None),
                ("noun", "feminine"), ("noun", "neuter"), (None, None), ("phrase", None),
                (None, None), ("noun", "masculine"),
            ])
            genders = [row[0] for row in db.execute(
                "SELECT card_id FROM questions WHERE kind='gender' ORDER BY card_id")]
            self.assertEqual(genders, [10, 14, 15, 19])
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO progress VALUES (1, 99999, 'x', '{}', NULL, 0, 1, 0)")
            db.rollback()
        after = self.snapshot()
        self.upgrade()
        self.assertEqual(self.snapshot(), after)

    def test_failed_upgrade_rolls_back_ddl_data_and_version_and_restores_fk_checks(self):
        # Inject a failure after questions was rebuilt, while backfilling the new fields.
        app = Flask(__name__)
        app.config["DATABASE"] = self.database
        app.teardown_appcontext(close_db)
        with app.app_context(), patch("fcard.db.classification", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                initialize()
            db = get_db()
            self.assertFalse(db.in_transaction)
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertNotIn("gender", [row[1] for row in db.execute("PRAGMA table_info(cards)")])
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE name='questions_v3'").fetchall(), [])
        self.assertEqual(self.snapshot(), self.before)
        self.upgrade()  # A failed attempt must not prevent a subsequent successful upgrade.

    def test_existing_fk_violation_aborts_instead_of_losing_orphaned_history(self):
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT INTO progress VALUES (1, 99999, 'x', '{}', NULL, 0, 1, 0)")
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "foreign-key check"):
            self.upgrade()
        self.assertEqual(self.snapshot(), before)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_separate_script_upgrades_v3_and_backup_retains_old_history(self):
        old_app = Flask(__name__)
        old_app.config["DATABASE"] = self.database
        old_app.teardown_appcontext(close_db)
        with old_app.app_context():
            db = get_db()
            db.execute("PRAGMA foreign_keys=OFF")
            with transaction(db):
                migrate_v2_to_v3(db)
        before = self.snapshot()
        backup = str(Path(self.temp.name) / "backup.sqlite3")
        runner = Path(__file__).resolve().parents[1] / "migrations/migrate.py"
        result = subprocess.run([sys.executable, str(runner), self.database, "--backup", backup],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Schema 3 -> 4", result.stdout)
        after = self.snapshot()
        for table in before:
            if table != "review_log":
                self.assertEqual(after[table], before[table], table)
        self.assertNotIn("review_log", after)
        with sqlite3.connect(backup) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT * FROM review_log ORDER BY rowid").fetchall(),
                             before["review_log"])
        again = subprocess.run([sys.executable, str(runner), self.database], capture_output=True)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.snapshot(), after)
        refuse = subprocess.run([sys.executable, str(runner), self.database, "--backup", backup],
                                capture_output=True)
        self.assertNotEqual(refuse.returncode, 0)
        self.assertEqual(self.snapshot(), after)

    def test_script_refuses_missing_database_without_creating_a_replacement(self):
        missing = Path(self.temp.name) / "missing.sqlite3"
        runner = Path(__file__).resolve().parents[1] / "migrations/migrate.py"
        result = subprocess.run([sys.executable, str(runner), str(missing)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(missing.exists())

    def test_concurrent_upgrade_is_once_only_and_preserves_all_old_progress(self):
        def upgrade(_):
            app = self.upgrade()
            with app.app_context():
                db = get_db()
                return (db.execute("PRAGMA user_version").fetchone()[0],
                        db.execute("SELECT COUNT(*) FROM questions WHERE kind='gender'").fetchone()[0])
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(upgrade, range(8))), [(4, 4)] * 8)
        self.assertEqual(self.snapshot()["progress"], self.before["progress"])
        self.assertNotIn("review_log", self.snapshot())
