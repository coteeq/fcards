from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest

from fcard import create_app, persistent_secret
from fcard.db import get_db


class BootstrapTest(unittest.TestCase):
    def test_key_is_persistent_and_concurrent_safe(self):
        with tempfile.TemporaryDirectory() as temp:
            with ThreadPoolExecutor(max_workers=4) as pool:
                keys = list(pool.map(lambda _: persistent_secret(temp), range(8)))
            self.assertTrue(all(key == keys[0] for key in keys))
            self.assertEqual(len(keys[0]), 32)
            self.assertEqual(Path(temp, "secret.key").stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(temp).iterdir()), [Path(temp, "secret.key")])

    def test_schema_startup_is_concurrent_safe(self):
        with tempfile.TemporaryDirectory() as temp:
            database = str(Path(temp) / "db.sqlite3")

            def bootstrap(_):
                app = create_app({
                    "TESTING": True, "DATABASE": database, "SECRET_KEY": "test",
                })
                with app.app_context():
                    return get_db().execute("PRAGMA user_version").fetchone()[0]

            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(list(pool.map(bootstrap, range(8))), [1] * 8)

    def test_nonempty_unversioned_database_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as temp:
            database = str(Path(temp) / "db.sqlite3")
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE unrelated (id INTEGER)")
            with self.assertRaisesRegex(RuntimeError, "nonempty, unversioned"):
                create_app({"DATABASE": database, "SECRET_KEY": "test"})
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute("SELECT name FROM sqlite_master "
                                            "WHERE type='table'").fetchall(), [("unrelated",)])


if __name__ == "__main__":
    unittest.main()
